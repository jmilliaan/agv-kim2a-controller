"""
drivers/mqtt_client.py — EVO fleet edge-node MQTT client [EVO]

The ONLY MQTT-aware module. It owns the paho-mqtt client in paho's own network
thread (loop_start) and bridges to the asyncio side only through `AMRState` +
`state.mqtt_out_queue`. It NEVER commands the drives or Modbus coils directly
(Key design rule 11) — incoming commands write single GIL-safe fields on
`AMRState`, and the mode_manager / mission FSM are the ones that act on them.

Command discipline (mqtt_design §5/§6):
  - per-AGV monotonic `seq` de-dups QoS-1 redelivery and store retries;
  - commands are NEVER retained;
  - the ACK means "received & accepted", NOT "physically done" — the physical
    effect arrives later as a state_change / traffic_hold *event*.

Threading:
  - `run(state)` is an asyncio task: it starts the paho thread, then awaits-drains
    `mqtt_out_queue` and publishes each (topic, payload, qos, retain).
  - on_connect / on_disconnect / on_message run on the paho network thread. They
    do GIL-safe single-field writes to AMRState and publish ACKs directly (paho
    publish is thread-safe). They never call asyncio queue methods.
"""

import asyncio
import json
import logging
import time

import paho.mqtt.client as mqtt

import config
import evo_topics as T

logger = logging.getLogger(__name__)


def _make_client(client_id):
    """Construct a paho client with v1-style callbacks on paho 1.x or 2.x."""
    try:
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=client_id,
            clean_session=True,
        )
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id, clean_session=True)


def _parse_cmd_leaf(topic: str):
    """`store/cmd/agv1/mission` -> 'mission'; None if not a command topic for us."""
    parts = topic.split("/")
    if len(parts) == 4 and parts[0] == "store" and parts[1] == "cmd":
        return parts[3]
    return None


class MQTTClient:
    def __init__(self, state):
        self.state = state
        self.client = _make_client(config.MQTT_CLIENT_ID)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # Last-Will: if this node drops ungracefully, the broker publishes offline.
        self._health_topic = T.agv_topic(config.MQTT_CLIENT_ID, T.AGV_HEALTH)
        self.client.will_set(
            self._health_topic,
            json.dumps({"ts": time.time(), "status": "offline"}),
            qos=1, retain=True,
        )

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def run(self, state):
        """Asyncio task: bring up the paho thread, then drain mqtt_out_queue."""
        logger.info("[MQTT] connecting to %s:%d as '%s'",
                    config.MQTT_BROKER_IP, config.MQTT_BROKER_PORT, config.MQTT_CLIENT_ID)
        try:
            self.client.connect_async(
                config.MQTT_BROKER_IP, config.MQTT_BROKER_PORT,
                keepalive=config.MQTT_KEEPALIVE,
            )
            self.client.loop_start()   # paho's own network thread
            while True:
                topic, payload, qos, retain = await state.mqtt_out_queue.get()
                try:
                    self.client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
                except Exception as e:
                    logger.warning("[MQTT] publish failed (%s): %s", topic, e)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                # Graceful goodbye overrides the retained LWT.
                self.client.publish(self._health_topic,
                                    json.dumps({"ts": time.time(), "status": "offline"}),
                                    qos=1, retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass

    # ── paho callbacks (network thread) ───────────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc, *args):
        if rc != 0:
            logger.error("[MQTT] connect failed rc=%s", rc)
            self.state.store_link_ok = False
            return
        self.state.store_link_ok = True
        # Announce liveness (retained) and subscribe to all our command leaves.
        client.publish(self._health_topic,
                       json.dumps({"ts": time.time(), "status": "online"}),
                       qos=1, retain=True)
        sub = T.cmd_topic(config.MQTT_CLIENT_ID, "#")
        client.subscribe(sub, qos=1)
        logger.info("[MQTT] connected; subscribed to %s", sub)
        self.state.log_event("INFO", "MQTT connected to store broker")

    def _on_disconnect(self, client, userdata, rc, *args):
        self.state.store_link_ok = False
        # Loss of the store link is NOT a motion fault (§9) — just log it.
        logger.warning("[MQTT] disconnected rc=%s (store link down — not a fault)", rc)

    def _on_message(self, client, userdata, msg):
        leaf = _parse_cmd_leaf(msg.topic)
        if leaf is None:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8")) if msg.payload else {}
        except (ValueError, UnicodeDecodeError):
            logger.warning("[MQTT] bad command payload on %s", msg.topic)
            return

        cmd_id = payload.get("cmd_id")
        seq    = payload.get("seq")

        # ── seq de-dup (mqtt_design §5/§6 rule 3) ─────────────────────────────
        if seq is not None:
            last = self.state.last_applied_seq
            if seq < last:
                self._ack(client, cmd_id, T.ACK_REJECTED, reason="stale_seq")
                return
            if seq == last:
                # exact retransmit of the already-applied command — idempotent
                self._ack(client, cmd_id, T.ACK_SUPERSEDED)
                return

        # ── route (writes AMRState only; mode_manager/mission FSM act on it) ──
        status, reason = self._route(leaf, payload)

        if status == T.ACK_ACCEPTED and seq is not None:
            self.state.last_applied_seq = seq

        self._ack(client, cmd_id, status, reason=reason)

    # ── command routing → AMRState ────────────────────────────────────────────
    def _route(self, leaf, payload):
        """Apply a command to AMRState. Returns (ack_status, reason_or_None)."""
        s = self.state

        if leaf == T.CMD_MISSION:
            mode = s.current_mode
            # A mission can only be accepted from a safe idle (armed) state.
            if mode not in ("armed", None):
                return T.ACK_REJECTED, f"not_idle:{mode}"
            stops = payload.get("stops", []) or []
            s.mission = {
                "trip_id":      payload.get("trip_id"),
                "loop":         payload.get("loop"),
                "active_stops": sorted(stops, key=lambda x: x.get("order", 0)),
                "loading":      payload.get("loading", {}),
            }
            s.mission_start_request = True
            logger.info("[MQTT] mission accepted trip=%s loop=%s stops=%d",
                        s.mission["trip_id"], s.mission["loop"], len(stops))
            return T.ACK_ACCEPTED, None

        if leaf == T.CMD_TRAFFIC:
            action = payload.get("action")
            if action == T.TRAFFIC_STOP:
                s.traffic_hold = True
                return T.ACK_ACCEPTED, None
            if action == T.TRAFFIC_GO:
                s.traffic_hold = False
                return T.ACK_ACCEPTED, None
            return T.ACK_REJECTED, f"bad_traffic_action:{action}"

        if leaf == T.CMD_CONTROL:
            action = payload.get("action")
            if action == "estop":
                s.cmd_estop = True
                return T.ACK_ACCEPTED, None
            if action == "reset":
                s.control_reset_request = True
                return T.ACK_ACCEPTED, None
            if action == "pause":
                s.commanded_pause = True
                return T.ACK_ACCEPTED, None
            if action == "resume":
                s.commanded_pause = False
                return T.ACK_ACCEPTED, None
            return T.ACK_REJECTED, f"bad_control_action:{action}"

        return T.ACK_REJECTED, f"unknown_leaf:{leaf}"

    # ── ack publish (paho thread; paho publish is thread-safe) ────────────────
    def _ack(self, client, cmd_id, status, reason=None):
        payload = {"ts": time.time(), "cmd_id": cmd_id, "status": status}
        if reason is not None:
            payload["reason"] = reason
        topic = T.agv_topic(config.MQTT_CLIENT_ID, T.AGV_ACK)
        try:
            client.publish(topic, json.dumps(payload), qos=1, retain=False)
        except Exception as e:
            logger.warning("[MQTT] ack publish failed: %s", e)
