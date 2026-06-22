# AGV Trolley-Dispatch System — MQTT Communication Design

**Document type:** Communication-layer design (broker, topic tree, QoS/retain, command semantics, liveness).
**Companion to:** `evo-system.md` (general system design). That document defines the entities, **loops** (allocation/pairing unit), dispatch logic, traffic arbitration concept, and failure modes referenced here.
**Model note:** This revision tracks the **loops** model in the system design (an AGV owns several fixed-route **loops**; pairing is **per loop**; empties load at a shared **attach** point, fulls unload at each AGV's **home**). The older flat **zone** model is superseded; topic placeholders use `{loop}` rather than `{zone}`.
**Audience:** LLM or engineer with basic AGV + MQTT knowledge, no prior context. Self-contained.
**Scope:** MQTT covers **store ↔ AGV coordination only.** Call buttons and LEDs are wired directly to the store controller and are **not** on MQTT. Topic *naming conventions* are provisional and may be renamed in a later phase; the structure and semantics are the deliverable.

---

## 1. Locked design decisions

| # | Decision | Choice | Consequence |
|---|---|---|---|
| 1 | Telemetry publish model | **Heartbeat + events** | Separate cheap periodic liveness beat *and* reliable event publishes for things the orchestrator/arbiter act on. |
| 2 | Between-tag position | **Tags-only (no odometry stream)** | Arbiter position input = one publish per tag read. Headway safety rests on **tag placement**, not MQTT cadence (see §10). |
| 3 | AGV-down detection | **Both** MQTT Last-Will + store-side heartbeat timeout | Two independent triggers for `single_agv` mode; whichever fires first wins. |
| 4 | Command reliability | **Explicit ACK** | Dedicated `ack` topic, per-command `cmd_id`, per-AGV monotonic `seq`, store retransmits until ACKed. |
| 5 | Current state availability | **Retained state topics** | Web app / reconnecting node reads current truth instantly. Commands are **never** retained. |
| 6 | Pre-junction tag placement | **Explicit latency-budget constraint** | Tag lead distance is sized from stop distance + command round-trip latency (see §10). |
| 7 | Store-state topics | **Keep** | Consistent multi-subscriber truth for monitors, at the cost of slightly more plumbing. |

**Taken as given:** broker = Mosquitto on the store PC (192.168.2.20); AGVs are clients; payloads are JSON; isolated /24 network, no internet.

---

## 2. Transport & broker

- **Broker:** Mosquitto on the store controller, `192.168.2.20:1883`.
- **Clients:** `store`, `agv1`, `agv2` — **stable client IDs** (required for Last-Will and per-client tracking).
- **Protocol:** MQTT 5 preferred (clean-start + session-expiry control; reason codes on ACK-like flows). MQTT 3.1.1 acceptable with `cleanSession=true`.
- **Auth:** isolated network — minimal/no auth acceptable for v1; add per-client credentials + ACLs later if required.
- **Encoding:** JSON, UTF-8.

---

## 3. Topic tree

Placeholders: `{id}` = `agv1` | `agv2`  ·  `{loop}` = `l1` | `l2` | `l3` | `l4`

### 3.1 AGV → Store
| Topic | Purpose |
|---|---|
| `agv/{id}/health` | online/offline liveness. **This is the Last-Will target.** |
| `agv/{id}/state` | full current snapshot — the single "truth" topic for the unit. |
| `agv/{id}/heartbeat` | ~1 Hz liveness + light status. |
| `agv/{id}/pos` | one publish per RFID tag read. **Arbiter fast path.** |
| `agv/{id}/event` | typed event stream: `state_change` / `confirm` / `fault_raised` / `fault_cleared` / `traffic_hold` / `traffic_resumed`. |
| `agv/{id}/ack` | command acknowledgments. |

### 3.2 Store → AGV
| Topic | Purpose |
|---|---|
| `store/cmd/{id}/mission` | trip assignment: loop id + ordered stops + loading order. |
| `store/cmd/{id}/traffic` | `stop` / `go` — collision arbitration. |
| `store/cmd/{id}/control` | `pause` / `resume` / `reset` / `estop`. |

### 3.3 Store state (for monitors)
| Topic | Purpose |
|---|---|
| `store/state/mode` | `dual_agv` / `single_agv`. |
| `store/state/queue/{loop}` | pending calls on the loop. |
| `store/state/dispatch/{loop}` | current prepare-trolley info — feeds the store-MP display. |

---

## 4. QoS, retain, session

| Topic | QoS | Retain | Rationale |
|---|---|---|---|
| `agv/{id}/health` | 1 | **yes** | LWT + last-known liveness must survive reconnects. |
| `agv/{id}/state` | 1 | **yes** | late joiner gets current truth instantly. |
| `agv/{id}/heartbeat` | 0 | no | high-rate, loss-tolerant; *absence* is the signal. |
| `agv/{id}/pos` | 1 | no | must not drop a tag-read feeding the arbiter. |
| `agv/{id}/event` | 1 | no | state/confirm/fault must arrive; it is a stream. |
| `agv/{id}/ack` | 1 | no | must arrive; correlated by `cmd_id`. |
| `store/cmd/{id}/*` | 1 | **no** | **commands are never retained** (§6). |
| `store/state/*` | 1 | yes | monitor snapshots (per-loop queue/dispatch, mode), consistent for late joiners. |

**Sessions:** all nodes use **clean session** (MQTT 5: `cleanStart=true`, `sessionExpiry=0`).
- AGVs must **not** receive stale QoS-1 commands redelivered after a reconnect — on restart they boot to **safe idle** (per system-design §16) and the store re-issues live commands.
- Retained **state** is broker-held and session-independent, so monitoring survives any client restart.

---

## 5. Command → ACK mechanics (explicit ACK)

1. Store publishes to `store/cmd/{id}/…` with a unique **`cmd_id`** and a **per-AGV monotonic `seq`**.
2. AGV validates `seq > last_applied_seq` and that the command is applicable in its current state, applies it, then publishes `agv/{id}/ack` = `{cmd_id, status}` where `status` ∈ `accepted` / `rejected(reason)` / `superseded`.
3. Store waits **~1 s** for the ACK. On timeout it **retransmits the same `cmd_id` + `seq`** (idempotent — the AGV de-duplicates by `seq`). After **3** failed retries → mark the AGV unhealthy and raise a fault.
4. **ACK ≠ completion.** ACK means "received and accepted." The physical effect arrives separately as a `state_change` / `traffic_hold` event on `agv/{id}/event`.

**Monotonic `seq` per AGV** makes QoS-1 redelivery and store retries idempotent — essential because stop/go is safety-relevant.

---

## 6. Three safety-critical rules

1. **Commands are never retained.** A retained `traffic:stop` or a stale `mission` replayed to a reconnecting AGV is a hazard. State is retained; commands are not.
2. **ACK ≠ stopped.** For `traffic:stop`, the arbiter treats the AGV as **still moving** until the `traffic_hold` *event* arrives — never on the ACK alone.
3. **Per-AGV monotonic `seq`** on every command guarantees idempotent retransmission and rejects out-of-order/stale commands.

---

## 7. Liveness detection (both mechanisms)

| Mechanism | Trigger | Default | Path |
|---|---|---|---|
| Heartbeat timeout (application) | N missed beats | 3 misses ≈ 3 s | faster |
| MQTT Last-Will (broker) | keepalive expiry | keepalive 3 s → will fires ≈ 4.5 s | backstop |
| Recovery | `health=online` + 2 fresh heartbeats | — | revert `single_agv` → `dual_agv` |

- Store subscribes `agv/+/health` (LWT) **and** tracks heartbeat arrival per AGV.
- Either path sets `store/state/mode`. Entering `single_agv` mode means **one AGV is down**: its loops **stall** (their calls wait — **no failover**, an AGV never serves another's loops), the surviving AGV keeps serving **only its own** loops, and the traffic arbiter can relax (effectively one mover), per system-design §11–12. Recovery reverts to `dual_agv` and the stalled loops resume.
- All thresholds are tunable.

---

## 8. Payload fields (tabular)

Common to every message: `ts` (timestamp). Commands and the streams that must order also carry `seq`.

| Topic | Key fields |
|---|---|
| `pos` | `ts`, `seq`, `tag_id`, `direction` (**`inbound`\|`outbound`**, relative to home) |
| `heartbeat` | `ts`, `seq`, `mission_state`, `last_tag` |
| `health` (retained, LWT) | `ts`, `status` (online\|offline) |
| `state` (retained) | `ts`, `mission_state`, `trip_id`, `last_tag`, `direction` (`inbound`\|`outbound`), `current_stop`, `fault{code, active}` |
| `event` | `ts`, `seq`, `type`, + type-specific: `state_change{from,to}` · `confirm{stop,location}` · `fault{code,detail}` · `traffic_hold{}` · `traffic_resumed{}` |
| `ack` | `ts`, `cmd_id`, `status`, `reason?` |
| `cmd/mission` | `ts`, `cmd_id`, `seq`, `trip_id`, `loop`, `stops[]{order, tag, trolley_type}`, `loading{front, rear}` |
| `cmd/traffic` | `ts`, `cmd_id`, `seq`, `action` (stop\|go) |
| `cmd/control` | `ts`, `cmd_id`, `seq`, `action` (pause\|resume\|reset\|estop) |
| `store/state/mode` (retained) | `ts`, `mode` (dual_agv\|single_agv) |
| `store/state/queue/{loop}` (retained) | `ts`, `calls[]{tbm, trolley_type, called_at}` |
| `store/state/dispatch/{loop}` (retained) | `ts`, `trip_id`, `front{tbm, type}`, `rear{tbm, type}`, `visit_order[]` |

---

## 9. Junction conflict conditioning (how `cmd/traffic` is driven)

This is the MQTT-side view of the traffic arbiter; the conflict rules themselves live in **system-design §11**. The arbiter is purely a function of the **`agv/+/pos` stream**.

- **Input:** the store subscribes to **`agv/+/pos`** and receives **every tag read from both AGVs**, each carrying `tag_id` + `direction` (`inbound`/`outbound`) + `ts`. No between-tag stream exists (decision #2), so each tag report is a discrete event the arbiter timestamps.
- **Rule shape (timing-window, not static snapshot):** a conflict fires when **AGV-x reports trigger tag A, then AGV-y reports trigger tag B within T seconds** (e.g. J2: `230`→`430` <15 s; J3: `430`→`12` <20 s). A *static* `(last_tag_x, last_tag_y)` match is **not** used — a retained/last tag is sticky and would mis-fire long after an AGV cleared (system-design §11.3). Windows are **tunable config**.
- **Hold:** the arbiter publishes **`store/cmd/{id}/traffic = stop`** to the lower-priority AGV. Per §6 rule 2 (ACK ≠ stopped), the held AGV is treated as **still moving until its `traffic_hold` event arrives** — never on the ACK.
- **Release (multi-condition latch):** the STOP clears on **any** of — the moving AGV reporting a designated downstream release tag (e.g. `6` for J1, `14` for J2/J3), **any** AGV reporting an `AGV n Home` tag, or a **100 s hard cap** — then the arbiter publishes `traffic = go`.
- **`seq` matters:** because stop/go is safety-critical, every `cmd/traffic` carries the per-AGV monotonic `seq` (§5, §6 rule 3) so a re-sent or reordered stop is idempotent.
- **Single-AGV mode:** with one AGV down, no second `pos` stream exists to satisfy any rule's second trigger, so the arbiter naturally issues no holds (consistent with disabling it, §7).

---

## 10. Pre-junction tag placement constraint (latency budget)

Because there is **no between-tag position stream** (decision #2), the traffic arbiter only learns an AGV's position when a tag is read. For a junction conflict, the AGV must come to rest **before** the conflict point. This imposes a **minimum lead distance** for the pre-junction tag.

**Critical chain** (tag-read → AGV halted):

| Step | Time component |
|---|---|
| AGV reads pre-junction tag, publishes `pos` (QoS 1) | t_pub |
| Network uplink AGV → broker → store | t_up |
| Store evaluates conflict lookup, decides stop | t_eval |
| Store publishes `traffic:stop` (QoS 1) | t_cmd |
| Network downlink store → broker → AGV | t_down |
| AGV reacts and begins braking | t_react |
| AGV physically decelerates to a halt | (distance, not time) |

> **Note:** the command **ACK** is *not* in this critical chain. The physical stop begins when the AGV receives the command; the ACK and the `traffic_hold` event are for the store's confidence, not for stopping timing.

**Required lead distance** of the pre-junction tag, before the latest-safe-stop point:

> lead_distance ≥ v_agv × (t_pub + t_up + t_eval + t_cmd + t_down + t_react) + stopping_distance(v_agv)

where `stopping_distance` is set by the AGV's max deceleration at speed `v_agv`.

**Design implications:**
- Pre-junction tags must be **commissioned per junction**, not at a uniform offset — lead distance scales with local AGV speed and the measured command latency on this network.
- Measure the real round-trip latency on the deployed Mosquitto + AP before fixing tag positions; budget headroom for AP congestion.
- If a junction's geometry cannot provide the required lead distance at full speed, the mitigation is a **speed cap on the approach segment** (reduces both `v×latency` and stopping distance), enforced via the mission/segment profile — not an MQTT change.
- This constraint is the MQTT-side reason the arbiter is **safe but coarse**: it cannot react faster than one tag-read, so the tag must be placed far enough out to absorb the full command round-trip plus braking.

---

## 11. Deferred / next phase

- Final topic naming conventions (this tree is structurally final, names provisional).
- Exact JSON schemas with field types, units, and validation.
- Concrete timer values (ACK timeout, retry count, heartbeat rate, keepalive, heartbeat-miss threshold) — tune against measured network behavior.
- Per-node finite-state machines (store orchestrator / AGV mission / call lifecycle) — these consume the topics defined here.
- Broker hardening: ACLs, per-client credentials, persistence settings, `max_inflight`, and connection limits.
- Measured latency + stopping-distance figures to populate the §10 budget per junction.
- Concrete junction-conflict timing windows and the 100 s release cap (§9) — tune against measured tag-read reliability and command latency.
