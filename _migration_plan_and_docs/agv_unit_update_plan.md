# AGV KIM2A Controller — Project Overview (CAN-BLDC variant)

> **⚠ Naming (read once):** this controller's code is **inherited from an older "TN / AGV B"
> project**. During migration, **replace those legacy labels with `EVO`**. The active profile is
> renamed to **`profiles/agv-evo-01.json`** (`AGV_ID=agv-evo-01`); the second unit is
> **`profiles/agv-evo-02.json`** (`agv-evo-02`). Any `agv_tn` / `AGV B` / `TN` token in cloned code,
> comments, or filenames is legacy and should be renamed (this document's §0 "ground truth" still
> says `agv_tn` because that is the file you literally cloned). **Wire identity is separate:** the
> on-MQTT client ids stay **`agv1`/`agv2`** (fixed by the store's `evo_topics.py`), carried in each
> profile's `mqtt.CLIENT_ID`/`AGV_INDEX` — so `agv-evo-01` ↔ `agv1`, `agv-evo-02` ↔ `agv2`. The
> rename must **not** leak onto the wire.
>
> **⚠ Vendored reference material (do this first):** the BLVD-KRD bring-up reference
> (`can_bldc/canopen_blv_r1.py`) and its CiA-402 object dictionary
> (`can_bldc/BLVD-KRD_CANopen_V400.eds`) currently sit at the **repo root**; **copy them into
> `agv-kim2a-controller/can_bldc/`** as the first Phase-A step so the repo is self-contained (the EDS
> is loaded at runtime — see [§6](#6-pid-controller--corepidpy--motionpy-bldc)).

> **Audience:** an LLM coding agent with no prior context on this project, its hardware, or its
> code. Read this before touching any file. It explains *what the system is*, *how it is built*,
> and *which protocols talk to which hardware*.
>
> **⚠ What this document is:** a **TARGET specification *and* implementation plan**, not a
> description of the code you just cloned. The agent's job is to **transform the cloned repo into
> the system described here.** The cloned repo (`agv_tn` profile, "AGV B / TN" towing unit) is a
> tape-following differential-drive AGV with a **pusher (towing-pin) actuator, lidar safety zones,
> an impact bumper, horns, and a web-editable RFID mapping store**, but its **motor drive is
> analog (Modbus AO)** and it has **no fleet/MQTT layer**. **[§0](#0-starting-point--migration-plan-read-first)
> is the migration plan — READ IT FIRST.** It states the real starting point and the ordered code
> changes; §1–§11 then describe the *target* in present tense.
>
> **⚠ Do NOT trust `README.md` or `project_overview.md`** in the cloned repo — both are **stale**
> (they describe an AGV-A / SLMP variant with a CANopen encoder and `pymcprotocol`, none of which
> is in the actual code). The actual code and **this document** are the sources of truth.
>
> **⚠ Motor-drive target [BLDC]:** in the target the two wheel motors are **Oriental Motor BLV
> type-R brushless DC gearmotors driven by BLVD-KRD CANopen drivers** over **CANopen / CiA-402**.
> Speed, direction, and braking are all commanded over CAN. *(The cloned code instead drives the
> wheels with a Modbus **AO speed DAC** + DO direction/brake coils — replacing this is **Phase A**,
> [§0.3](#03-phase-a--motor-drive-swap-analog--can-bldc).)* The Modbus **DIO module** is retained
> only for buttons/lidar/bumper inputs and pusher/horn outputs. Sections that changed are flagged
> **[BLDC]**.
>
> **⚠ EVO fleet integration (this is a PORTING GUIDE):** the codebase as described above is a
> **standalone single-AGV controller**. This document has been extended to guide porting it into
> the **EVO trolley-dispatch fleet**, where this unit becomes **one of two AGV edge nodes** (`agv1`
> / `agv2`) commanded by a central **store controller** over **MQTT**. The fleet-level architecture
> lives in the companion docs **`evo-system.md`** (system design) and **`evo-system_mqtt_design.md`**
> (MQTT comms). **MQTT is not yet implemented in the AGV** — the sections flagged **[EVO]** below
> describe *what must change in the code* to reach that target. Where an [EVO] change conflicts with
> existing behaviour, the existing text is edited in place (notably **IP addressing** and the
> **removal of physical reverse mode**); everything else is additive. Standalone operation is
> **retained as a fallback** (feature-flagged — see [§4](#4-configuration--configpy--profile-json-bldc)).
> **Flag legend:** `[BLDC]` = changed by the CAN-BLDC motor swap; `[EVO]` = changed/added by the
> fleet integration.
>
> **⚠ SENSOR CHANGE — magnetic guide sensor: Roboteq MGS1600 → SICK MLS (MLSE) [SENSOR]:** the
> magnetic line sensor has been replaced. Wherever this document says "MGS1600" / "Roboteq" /
> "custom CAN frame", the deployed sensor is now a **SICK MLS**, a true **CANopen** device that
> publishes its line position on **TPDO1** (COB-ID `0x180 + node id`; factory node `0x0A` → 0x18A).
> It still **shares the one CANable2 bus** with the two BLVD-KRD drives and is still read by
> subscribing to its COB-ID on the shared `canopen.Network` (no second adapter). Key deltas:
> the driver is **`drivers/can_mls.py`** (replaces `drivers/can_mgs1600.py`); the steering process
> variable is the MLS **LCP2** line center point (a single detected line is always output as LCP2),
> carried in the unchanged `latest_sensor["left_mm"]` so the PID / dashboard / FSM are untouched;
> markers are now **numeric codes** (`marker_code`), not left/right (the active profile uses only
> RFID triggers, so this is inert today); and the MLS **must be pre-set to 500 kbit/s** (factory is
> 125 kbit/s) + a free node id via the SICK config tool / LSS **before** joining the shared bus —
> bitrate is not set from code. The vendored EDS + reference reader live in `can_magnetic_sensor/`.
> The MGS1600 wording in §0.1 / §1 / §2a / §6 below is **superseded by this note**.

---

## 0. Starting point & migration plan (READ FIRST)

This document's §1–§11 describe the **target** in present tense (as if BLDC + EVO are already done).
**They are not.** This section is the bridge: what the cloned repo actually contains today, and the
ordered code changes to reach the target.

### 0.1 What you actually cloned (ground truth)

The repo is a **standalone, analog-drive** controller. Architecture (asyncio event loop + Flask
thread, one shared `AMRState`, queue-based I/O, the `mode_manager` FSM, the declarative
SequenceEngine, the RFID mapping store) is **exactly what the target keeps** — only the **motor
drive** and the **fleet layer** change. Concretely, today:

| Aspect | Cloned reality (keep in mind) | Target |
|---|---|---|
| **Motor drive** | **Modbus AO speed DAC + DO direction/brake coils.** `drivers/modbus_ao.py` (`AOWriter`) consumes `ao_queue`; `motion._drive()` writes `do_fwd`/`do_rev`/`do_brake` + `ao_speed`; `motion.rpm_to_voltage`/`voltage_to_rpm`; output clamped to `AO_MAX_VOLTAGE` (5.0 V). | CAN-BLDC / CiA-402 (`drivers/can_bldc.py`, `motor_queue`) — **Phase A** |
| **Motor config** | `config.AO_IP`, `AO_BASE`/`NUM_AO`, `V_RANGE`/`DAC_RES`, `AO_MAX_VOLTAGE`, `MOTOR_CHANNELS{do_fwd,do_rev,do_brake,ao_speed}`; profile `hardware{}` block | `motor_can{}` block — **Phase A** |
| **Networks** | **three** Modbus-side subnets: controller `192.168.2.100`, DIO+RFID `192.168.3.x` (`.30`/`.200`), **AO module `192.168.1.30`**; CAN carries the **MGS1600 sensor only** | fleet `192.168.2.22/.24` + store `.20`; AO subnet gone; CAN also carries the two drives — **Phase A + B** |
| **Reverse mode** | **fully implemented** (`auto_mode(direction="reverse")`, `current_mode="reverse"`, `state.reverse_auto_request`, `/api/reverse_auto`, horn `_AUTO_MODES=("running","reverse")`) | removed; `direction` becomes a logical flag — **Phase B** |
| **Fleet / MQTT** | **none.** No `paho-mqtt`, no broker, no mission/traffic/confirm | full MQTT edge node — **Phase B** |
| **Deps** (`requirements.txt`) | `pymodbus`, `python-can` (sensor), `pyserial`, `Flask`, `matplotlib` | + `canopen` (Phase A), + `paho-mqtt` (Phase B) |
| **Unchanged** | DIReader/DOWriter, `can_mgs1600` sensor, `rfid_tcp`, `core/pid.py` math, `core/sequence_engine.py`, `core/mapping_store.py`, pusher/lidar/bumper/horn logic, the dashboard shell | same |

> The PID controller math, the sequence engine, the mapping store, and the whole DI/pusher/lidar
> stack are **unaffected** — Phase A only swaps the *output stage* (rpm → CANopen instead of
> rpm → voltage → DAC), and Phase B wraps the existing FSM with an MQTT layer.

### 0.2 Migration overview — two phases

- **Phase A — motor drive: analog → CAN-BLDC.** Makes the `[BLDC]`-flagged sections true. Pure
  output-stage swap; no fleet logic. After Phase A the unit still runs **standalone** exactly as
  before, just driving the wheels over CANopen.
- **Phase B — standalone → EVO fleet (MQTT).** Makes the `[EVO]`-flagged sections true. Adds the
  MQTT edge-node layer, mission FSM, traffic-hold, confirm gating, and removes reverse. Detailed as
  the ordered checklist in **[§11](#11-phase-b--evo-fleet-integration-checklist-evo)**.

Do **Phase A first** (motion must work before fleet coordination means anything). Each phase leaves
the AGV in a runnable, testable state.

### 0.3 Phase A — motor drive swap (analog → CAN-BLDC)

Per-file work. The **target behaviour** of each new/changed symbol is specified in
[§6](#6-pid-controller--corepidpy--motionpy-bldc) (motion + `CANMotorDriver`); this table is the
*delta from the cloned code*.

| File | In the cloned code | Change |
|---|---|---|
| `can_bldc/` | the two files were dropped at the **repo root** | **COPY into `agv-kim2a-controller/can_bldc/` FIRST** — `canopen_blv_r1.py` (bring-up reference) + `BLVD-KRD_CANopen_V400.eds` (object dictionary, loaded at runtime). The repo must be self-contained. |
| `requirements.txt` | no `canopen` | **add `canopen`** (pulls `python-can`, already present) |
| `drivers/can_bldc.py` | — (absent) | **CREATE** `CANMotorDriver`: owns the `canopen.Network`, two `BaseNode402` drives (left=node 1, right=node 2), walks the CiA-402 state machine, consumes `motor_queue` → `Target velocity` (`0x60FF`), reads back `Velocity actual`/`Statusword` for telemetry + fault. Resolve the EDS path **relative to the package root** (`os.path.join(_dir, "can_bldc", config.MOTOR_CAN["EDS"])`), not the cwd. See [§6 motor driver](#the-motor-driver--driverscan_bldcpy-canmotordriver-bldc). |
| `drivers/modbus_ao.py` | `AOWriter` → `ao_queue` | **DELETE** (and the AO test `_debugging/analog_output.py`). Also **rewrite-or-remove `_debugging/motor_test.py`** — it `import`s `motion.rpm_to_voltage` (deleted here) and writes the AO DAC, so it breaks on Phase A. |
| `state.py` | `ao_queue` (line ~97) | **rename → `motor_queue`** (tuples `(side, target_rpm)`); add drive-telemetry fields `motor_actual_rpm`/`motor_statusword`/`motor_fault` to `PerceptionState` |
| `motion.py` | `voltage_to_rpm`/`rpm_to_voltage`; `_drive()` writes DO dir/brake + `ao_speed`; `update_voltages`; `set_cat1_stop`/`set_brake` toggle a brake **coil** | **remove the voltage helpers**; `set_forward/reverse/left/right/...` emit **signed rpm** onto `motor_queue`; `update_voltages`→**`update_velocities(left_rpm,right_rpm)`**; `idle`=0 rpm/Operation-Enabled; `set_brake`/`set_cat1_stop`=0 rpm + **CiA-402 Quick stop/Halt**. **Keep pusher_* unchanged** (still DO, keep the 200 ms relay deadtime). |
| `modes.py` | `auto_mode` output stage: `rpm_to_voltage()` → clamp `AO_MAX_VOLTAGE` → `ao_queue.put((0/1,v))` (lines ~234-238); `_flush_and_idle`/`_flush_and_brake` flush `ao_queue`; `manual_mode` ramps **voltages** | output stage queues **rpm** to `motor_queue` (clamp `±MOTOR_MAX_RPM`); flush `motor_queue`; `manual_mode` ramps **rpm**. *(Reverse stays for now — removed in Phase B.)* |
| `config.py` | loads `AO_IP`, `AO_BASE`/`NUM_AO`, `V_RANGE`/`DAC_RES`, `AO_MAX_VOLTAGE`, `MOTOR_CHANNELS` | drop those; **load `motor_can`** (node ids, `invert`, `BITRATE`, `EDS`, `PROFILE_ACCEL/DECEL`, `QUICKSTOP_DECEL`, `MOTOR_MAX_RPM`) — see [§4](#4-configuration--configpy--profile-json-bldc) |
| `profiles/agv_tn.json` | `networking.AO_IP`, `hardware{V_RANGE,DAC_RES}`, `ao_max_voltage`, `motor_channels`, `io_mapping.AO_BASE/NUM_AO` | remove those; **add `motor_can{}`**; drop the dead `192.168.1.x` AO subnet |
| `main.py` | imports/instantiates `AOWriter`; `shutdown()` zeroes AO holding registers (lines ~30-34, 72) | instantiate **`CANMotorDriver`** (always-on, in `core_tasks`); `shutdown()` → command 0 rpm, Quick stop, CiA-402 down to `SWITCHED ON`, then zero DO coils |
| `safety_watchdog.py` | critical fault path does `ao_queue.put((0,0.0))`/`(1,0.0)` (lines ~49-50) | command **both drives to zero velocity / Quick stop**; add the **drive-FAULT → `motor_fault` → `system_error`** tier ([§9](#9-safety-watchdog-horn--shutdown)) |
| `drivers/can_mgs1600.py` | opens its **own** `can.interface.Bus` (slcan) for the sensor | the drives + sensor **share one CAN adapter** — `CANReader` must **stop constructing its own `Bus`** and instead consume the shared `canopen.Network`/bus owned by `CANMotorDriver`, keyed on `SENSOR_COB_ID` (0x186). Concrete coexistence pattern in [§2a](#2a-the-shared-can-bus-bldc). |

**Phase A done-when (two tiers):**
- **Static / dev-box (no hardware, runnable on Windows):** `rg -i 'ao_queue|AO_|voltage_to_rpm|rpm_to_voltage'`
  finds nothing in live code; `AGV_ID=agv-evo-01 python -c "import main"` succeeds; the CANopen object
  dictionary + two `BaseNode402` build against a **virtual** bus (`interface="virtual"`) — see
  [Hardware-free develop & validate](#hardware-free-develop--validate-bldc-evo).
- **On-vehicle:** the AGV tape-follows forward + reverse and jogs in manual, driven entirely over
  CANopen; a tripped drive surfaces as `system_error`.

### 0.4 Phase B — EVO fleet integration

Once Phase A runs, do Phase B per **[§11](#11-phase-b--evo-fleet-integration-checklist-evo)** (MQTT
client, mission FSM, traffic-hold, confirm gating, liveness, dashboard role change, reverse removal).

---

## 1. What this is

A controller for an **Autonomous Guided Vehicle (AGV)** — a differential-drive (two
independently-driven wheels) robot that follows a **magnetic guide tape** laid on the floor. It
drives a fixed tape loop, slows for corners and stations (driven by RFID floor tags), stops to
raise/lower a **towing pin (pusher)** to couple/decouple a load, and protects itself with lidar
zones and an impact bumper.

- **Language / runtime:** Python 3.11+, `asyncio`. One event loop runs all control logic
  concurrently; a Flask web dashboard runs in a separate daemon thread.
- **Entry point:** [main.py](main.py)
- **Run:** `AGV_ID=agv-evo-01 python3 main.py`
- **Deployment target:** an Ubuntu PC mounted on the AGV. The repo is developed on Windows but
  **cannot be run there** — the hardware drivers (Modbus device, CANable USB adapter, BLVD-KRD
  drives) only exist on the vehicle.
- **Config selection:** the `AGV_ID` environment variable picks a profile under
  [profiles/](profiles/). The cloned code default is `agv1_kim` ([config.py](config.py)) — **change
  it / pass `AGV_ID` explicitly.** The cloned branch ships only the legacy `agv_tn.json`, which is
  **renamed to [profiles/agv-evo-01.json](profiles/agv-evo-01.json)** during migration; run with
  `AGV_ID=agv-evo-01`. All per-vehicle tuning lives in that JSON; there is no code change between
  vehicles.
- **Fleet identity [EVO]:** the profile name is the **local** unit id (`agv-evo-01` / `agv-evo-02`);
  the **wire** MQTT client id stays `agv1` / `agv2` (store contract, via `mqtt.CLIENT_ID`), and the
  **fleet IP** is `192.168.2.22` / `.24`. Provide a profile per unit
  (`profiles/agv-evo-01.json`, `profiles/agv-evo-02.json`) carrying its `agv_index`, fleet IP, and
  the shared `mqtt` block ([§4](#4-configuration--configpy--profile-json-bldc)). The two units run
  the **same code**; only the profile differs.

### Physical I/O at a glance **[BLDC]**

| Subsystem | Hardware | Bus / protocol | Library |
|---|---|---|---|
| Digital inputs (buttons, selectors, lidar, bumper) | Remote DIO module | **Modbus TCP** (discrete inputs) | `pymodbus` (async) |
| Digital outputs (pusher relays, horns) | Remote DIO module | **Modbus TCP** (coils) | `pymodbus` (async) |
| **Wheel motors (×2)** | Oriental Motor **BLV type-R** BLDC + **BLVD-KRD** drivers | **CANopen / CiA-402** (Profile Velocity) over **slcan** / CANable2 USB | `canopen` |
| Magnetic guide sensor **[SENSOR]** | ~~Roboteq MGS1600~~ → SICK **MLS (MLSE)** | **CANopen** TPDO1 (COB-ID 0x18A) over **slcan** / CANable2 USB | `canopen` / `python-can` |
| RFID floor-tag reader | TCP RFID reader | **raw TCP socket** (hex stream) | `socket` |
| Web dashboard | operator phone/laptop over WiFi | HTTP | `Flask` |
| **Fleet coordination [EVO]** | Store controller (MQTT broker) | **MQTT** (pub/sub, JSON) over WiFi | `paho-mqtt` |

> The motors and the MGS1600 **share a single CAN bus** (one CANable2 adapter, 500 kbit/s) —
> see [§2a](#2a-the-shared-can-bus-bldc).

Dependencies are pinned in [requirements.txt](requirements.txt). This variant adds **`canopen`**
(which itself pulls in `python-can`).

### Network topology **[BLDC] [EVO]**

The Ubuntu controller has **multiple NICs / VLANs** plus the USB-CAN adapter. Each device group
lives on its own segment:

| Segment | Devices |
|---|---|
| `192.168.2.x` (**fleet net** [EVO]) | **This AGV: `.22` (agv1) / `.24` (agv2)**; **store controller `.20`** (MQTT broker `:1883`, operator web app); Wi-Fi AP `.254`. Local Flask **monitor** on this unit at `:5000`. |
| `192.168.3.x` (field net) | DIO module (`.30`) — Modbus TCP :502; RFID reader (`.200:2022`) |
| **CAN** (slcan, 500 kbit/s) | MGS1600 sensor + BLVD-KRD left (node 1) + BLVD-KRD right (node 2) |

> **[EVO] IP-addressing change (conflict resolved):** the standalone build put the controller at
> **`192.168.2.100`** with its own HMI WiFi. In the fleet that address is **retired** — the unit
> takes a **per-AGV fleet address** (`.22` / `.24`, from its profile) on the shared
> `192.168.2.x` network alongside the store (`.20`) and AP (`.254`), per `evo-system.md` §4. The
> **`192.168.3.x` field segment (DIO + RFID) is unchanged** and stays on its own NIC — only the
> fleet-facing NIC's address moves. The Flask app at `:5000` survives but is now a **monitor +
> manual fallback** ([§10](#10-web-dashboard--appapppy--apptemplates-bldc)), not the primary
> control surface.

---

## 2. Process & concurrency model **[BLDC]**

```
asyncio event loop (single thread)
├─ CANMotorDriver.run      — consumes motor_queue → CiA-402 Target velocity (both drives) [always]
├─ DOWriter.run            — consumes do_queue → Modbus coils (pusher, horns)             [if DIO_ENABLED]
├─ DIReader.run            — Modbus discrete-input poll → latest_di + di_queue            [if DIO_ENABLED, watched·critical]
├─ CANReader.run           — MGS1600 magnetic sensor → latest_sensor + queue              [if CAN_ENABLED, watched·auto-only]
├─ RFIDReader.run          — TCP RFID → rfid_queue                                        [if RFID_ENABLED, watched·auto-only]
├─ safety_watchdog         — polls sensor-driver health every 50 ms                      [always]
├─ rfid_processor          — drains rfid_queue → SequenceEngine.on_rfid_tag() (+ pos publish) [always]
├─ horn_controller         — drives regular/alarm horn DO channels                       [always]
├─ MQTTClient.run          — broker connect/LWT; sub cmd/*; drains mqtt_out_queue → publish [if FLEET_MODE] [EVO]
├─ heartbeat_publisher     — ~1 Hz agv/{id}/heartbeat + state refresh                    [if FLEET_MODE] [EVO]
└─ modes.mode_manager      — the central FSM; spawns manual/auto tasks                   [always]

Flask daemon thread (separate): serves the dashboard, mutates a few state fields.
```

The `CANMotorDriver` and the MGS1600 sensor share one physical CAN bus brought up at boot (see [§2a](#2a-the-shared-can-bus-bldc)).

### 2b. Fleet coordination tasks **[EVO]**

When `FLEET_MODE` is on, the MQTT layer is added as **edge-node** plumbing — it never touches
hardware directly; it only reads/writes `AMRState` and the queues, so the existing control loop is
unchanged:

- **`MQTTClient.run`** — owns the `paho-mqtt` client (stable client id `agv1`/`agv2`), registers the
  **Last-Will** on `agv/{id}/health=offline`, subscribes to `store/cmd/{id}/#`, and **drains
  `mqtt_out_queue`** (pos / event / ack / state publishes). Incoming commands are validated by `seq`
  (monotonic, per-AGV) and routed: `mission` → `state.mission` (+ wake `mode_manager`), `traffic` →
  `state.traffic_hold`, `control` → mapped control action. Every accepted/rejected command emits an
  `ack`.
- **`heartbeat_publisher`** — emits `agv/{id}/heartbeat` (~1 Hz, QoS 0) and refreshes the retained
  `agv/{id}/state` snapshot; this is the store's primary liveness signal
  ([§9](#9-safety-watchdog-horn--shutdown)).
- **`pos` publishing** is folded into the existing `rfid_processor`: on **every localizing tag read**
  it puts a `pos` message (`tag_id` + logical `direction`) on `mqtt_out_queue` **before** local
  sequence dispatch, so the store's traffic arbiter sees the full tag stream.
- **`event`/`ack`** are produced wherever the relevant transition happens (`mode_manager`,
  confirm handling, fault paths) by putting a typed message on `mqtt_out_queue`.

**The `paho-mqtt` client runs in its own thread** (its network loop is blocking); like the Flask
thread it only does GIL-safe single-field writes + queue puts, honouring the never-block-the-loop
rule. `mqtt_out_queue` is the one bridge from the asyncio side to the MQTT thread.

Drivers are **instantiated once at boot** from the feature flags ([main.py](main.py)). There is
**no live driver toggling / DriverManager** — feature flags are read from the profile at startup
and are fixed for the process lifetime. (Some *behaviour* toggles — lidar stop/slow, RFID
dispatch, auto speeds — are live-tunable from the dashboard via `state.tuning`, but they do not
start/stop drivers.)

All tasks share **one** [`AMRState`](state.py) object passed by reference. There is no message
passing between tasks except through the queues inside that object. The Flask thread **and the MQTT
client thread [EVO]** touch the same object — GIL-safe for reads and for the single-attribute writes
they perform (`web_manual_command`, `web_pusher_request`, tuning fields, `mapping_reload_pending`,
and the [EVO] command fields `mission`, `traffic_hold`, control requests). *(The old
`reverse_auto_request` field is removed — see [§5](#5-mode-state-machine--modespy).)*

**Golden rule:** never block the event loop. The CANopen master (SDO transfers, NMT/state-machine
transitions, bus construction) is synchronous, so it runs through `loop.run_in_executor(...)`; the
Modbus/RFID paths use the async pymodbus / non-blocking-socket APIs directly.

### 2a. The shared CAN bus **[BLDC]**

One CANable2 (slcan, 500 kbit/s) carries **both** protocols, distinguished by COB-ID. **You cannot
open the same slcan serial device twice**, so there is exactly **one** bus object and
`CANMotorDriver` owns it:

- `CANMotorDriver` calls `network.connect(interface="slcan", channel=<config>, bitrate=500000)`
  **once** at boot on a single `canopen.Network`, then `network.add_node(canopen.BaseNode402(
  node_id, EDS))` for **left = node 1** and **right = node 2** (the BLVD-KRD identifies as Oriental
  Motor vendor **702**, product **5111**). The `EDS` path resolves relative to the package root
  (`can_bldc/BLVD-KRD_CANopen_V400.eds`), not the cwd.
- The **channel is OS/config-driven.** On the Ubuntu vehicle it is a slcan device such as
  `/dev/ttyACM0` (or a `slcanX` netdev) — **not** `COM16`; that value in the bring-up reference
  ([can_bldc/canopen_blv_r1.py](can_bldc/canopen_blv_r1.py)) is only the Windows dev port. It comes
  from `motor_can.CHANNEL` in the profile.
- **[SENSOR] (updated):** the magnetic sensor is now a **SICK MLS** CANopen device emitting
  **TPDO1** at COB-ID `SENSOR_COB_ID` (`0x180 + node id` = **0x18A** for node 0x0A). *(The old
  MGS1600 used a custom frame at 0x186 — superseded.)* The pattern is otherwise unchanged:
  **`CANReader` must not construct its own `can.interface.Bus`** — it consumes the **same** bus
  owned by `CANMotorDriver` via `network.subscribe(SENSOR_COB_ID, callback)` (canopen forwards the
  COB-ID to subscribers). The implemented driver is **`drivers/can_mls.py`** and `CANReader.run`
  **accepts the shared network** rather than opening slcan a second time.
- Because the bus is needed for *motion*, it is always brought up. `CAN_ENABLED` gates only the
  **sensor reader / auto-follow path**, not the bus itself.

---

## 3. Shared state — [state.py](state.py)

`AMRState` is the single source of truth. It is split into typed sub-domains, but every field is
also exposed as a **flat property shim** on `AMRState` so call sites can read/write
`state.current_mode`, `state.speed_mode`, etc. directly.

### Cross-task channels — all `asyncio.Queue` **[BLDC]**

Both inputs **and** outputs use queues on this branch (an earlier "latest-wins setpoint table"
design was reverted):

- **Input queues**, drained *keep-latest* every cycle so stale frames never accumulate:
  - `di_queue` — DI bit arrays (DIReader → manual_mode)
  - `sensor_queue` — MGS1600 CAN frames (CANReader → auto_mode; mode_manager peeks it on START to
    confirm tape)
  - `rfid_queue` — 4-char hex tag strings (RFIDReader → rfid_processor)
- **Output queues**, consumed FIFO by the writer tasks:
  - `motor_queue` — `(side, target_rpm)` tuples → CANMotorDriver (signed motor r/min; sign = wheel
    direction).
  - `do_queue` — `(channel, bool)` tuples → DOWriter (pusher relays, horns only)
  - `mqtt_out_queue` **[EVO]** — typed `(topic, payload)` messages → MQTTClient thread (pos / event
    / ack / state). This is the single bridge from the asyncio side to the MQTT network thread.

  Because these are FIFO queues, code that needs a clean slate (mode transitions, emergency)
  **explicitly flushes** them with `get_nowait()` before asserting a new motion command — see
  `_flush_and_idle` / `_flush_and_brake` in [modes.py](modes.py). Motion helpers in
  [motion.py](motion.py) `put()` onto these queues; nothing writes the CAN drives or Modbus coils
  directly except `shutdown()`.

### State domains **[BLDC]**

| Domain | Owner(s) | Key fields |
|---|---|---|
| `SystemState` | mode_manager, safety_watchdog | `current_mode` (`None`/`manual`/`armed`/`running`/`emergency`), `emergency_active`, `system_error`(+detail), `sensor_error`(+detail), `bumper_active`, `event_log` |
| `KinematicState` | sequence_engine, mode_manager | `speed_mode` (`HIGH`/`SLOW`/`EXTRA_SLOW`), `sequence_stop`, `pending_sequence`, `web_manual_command`+`_ts`, `motion_telemetry`, `last_rfid_tag`+`_ts`, `web_pusher_request`, `at_home`, `end_cycle_request`, `unmapped_rfid_log` |
| `PerceptionState` | hardware drivers, auto_mode | `latest_di`, `latest_do`, `latest_sensor`, `can_last_rx`; **drive telemetry** `motor_actual_rpm[left/right]`, `motor_statusword[left/right]`, `motor_fault` |
| `TuningState` | Flask thread, auto_mode | `mapping_reload_pending`, `lidar_stop_enabled`, `lidar_slow_enabled`, `rfid_enabled`, `auto_high_speed`/`auto_slow_speed`/`auto_extra_slow_speed` |
| `FleetState` **[EVO]** | MQTTClient thread, mode_manager | `mission` (`loop`, ordered `active_stops[]`, `loading{front,rear}`, `trip_id`), `mission_state` (EVO FSM, [§5](#5-mode-state-machine--modespy)), `direction` (`outbound`/`inbound`, logical), `traffic_hold` (commanded stop latch), `confirm_pending`+`confirm_ts`, `last_applied_seq` (per-AGV monotonic, command de-dup), `store_link_ok` (broker connected) |

> **[EVO] note — removed/replaced fields:** `reverse_auto_request` is **gone** (physical reverse
> mode removed). `current_mode` no longer has a `reverse` value. The new `direction` field is a
> *logical* inbound/outbound flag (not a drive direction) used for `pos` reports and confirm/return
> logic.

`motion_telemetry` now carries the **commanded** per-wheel rpm and PID terms; the CiA-402
**actual** velocity (object `0x606C`, *Velocity actual value*) read back from each drive's TPDO is
exposed separately as drive telemetry (no separate calibration encoder is needed — the BLV-R has
its own).

`state.log_event(level, msg)` appends a timestamped entry to `event_log` (capped at 200), which
backs the `/errors` dashboard page. Runtime auto-speeds are hard-bounded by
`AUTO_SPEED_MIN`/`AUTO_SPEED_MAX` (module constants in [state.py](state.py)).

---

## 4. Configuration — [config.py](config.py) + profile JSON **[BLDC]**

`config.py` loads `profiles/{AGV_ID}.json` at import (falling back to a legacy `parameters.json`),
and flattens it into module-level constants. **There is no fail-fast `_validate()` step** on this
branch — a malformed JSON raises at parse time, but out-of-range values are not range-checked.

Profile sections (see [profiles/agv-evo-01.json](profiles/agv-evo-01.json), the renamed `agv_tn.json`):

- **`networking`** — DIO/RFID IPs/ports in §1, **plus the fleet address** (`.22`/`.24`) on the
  `192.168.2.x` net **[EVO]** (the old `.100` controller address is removed — see §1).
- **`sensor_orientation`** *(top-level)* — `1` = normal, `-1` = sensor mounted rear-facing
  (flips the PID error sign). `agv-evo-01` = `-1`.
- **`io_mapping`** — `DI_BASE/DO_BASE`, `NUM_DI/NUM_DO`, and the *logical* DI bit indices:
  `DI_EMERGENCY`, `DI_MODE_SWITCH` (+ `MODE_SWITCH_INVERT`), `DI_START`, `DI_RESET`,
  `DI_FWD/REV/LEFT/RIGHT`, `DI_LIDAR_OUTER/SLOW/STOP` (each optional → `None`), `DI_BUMPER`
  (optional), **`DI_CONFIRM`** *(new, [EVO])* — the onboard confirm button
  ([§5](#5-mode-state-machine--modespy)). `DI_FLIPPED` inverts all DI bits at read time.
- **`motor_can`** — per-side CANopen
  setup: `{ "left": {"node_id": 1, "invert": false}, "right": {"node_id": 2, "invert": true} }`
  plus bus-level `CHANNEL` (slcan device, e.g. `/dev/ttyACM0`), `BITRATE` (`500000`), `EDS`
  (filename under `can_bldc/`, resolved relative to the package root — **not** the cwd),
  `PROFILE_ACCEL`/`PROFILE_DECEL` (→ 0x6083 / 0x6084), `QUICKSTOP_DECEL` (→ 0x6085), and
  `MOTOR_MAX_RPM` (the Target-velocity clamp). **Provisional starting values (write these so the code
  runs; tune on vehicle):** `PROFILE_ACCEL=PROFILE_DECEL=2000` (the bring-up reference value),
  `QUICKSTOP_DECEL=2000`, `MOTOR_MAX_RPM` a sane ceiling well under the object hard-limit
  ±4,000,000. `invert` flips the velocity sign for a mirror-mounted wheel (equivalently the drive's
  *Motor rotation direction* object `0x41A4`). **Motion never hardcodes node IDs; it always reads
  this map.**
- **`pusher_channels`** — `{extend: [...], retract: [...]}` DO indices for the double-acting
  linear actuator (towing pin). `None` = no pusher.
- **`horn_channels`** — `{regular_horn, alarm_horn}` DO indices. `None` = no horn.
- **`kinematics`** — `WHEEL_DIAMETER`, `GEAR_RATIO` (→ `WHEEL_CIRCUMFERENCE`). `GEAR_RATIO` is the
  **wheel gearbox** ratio used by `mps_to_rpm`; the BLV-R's own internal gear ratio (object
  `0x6091`) is configured in the drive and is *not* re-applied in software.
- **`speeds`** — manual & auto HIGH/SLOW + auto EXTRA_SLOW targets (m/s), `ACCEL_RATE`,
  `MANUAL_ACCEL_RATE`. (The software `ACCEL_RATE` ramp shapes the *commanded* velocity; the drive
  additionally enforces its own `PROFILE_ACCEL`/`PROFILE_DECEL` S-curve.)
- **`pid_tuning`** — two gain sets (HIGH = `KP/TD/N`, SLOW = `KP_SLOW/TD_SLOW/N_SLOW`), shared
  `TI/TI_DEADBAND/TI_MAX/DT`, `OUTPUT_CLAMP_RPM`, speed-reduction `V_RED_COEF[_SLOW]`/`SR_ALPHA`/
  `SR_CAP`. PID output is in **rpm** and feeds Target velocity directly. See [§6 PID](#6-pid-controller).
- **`can_sensor`** — `SENSOR_COB_ID`, flag bitmasks
  (`FLAG_TAPE_DETECT/LEFT_MARKER/RIGHT_MARKER/SENSOR_FAIL`), `CAN_TIMEOUT`, `CAN_NODE_ID`.
- **`rfid`** — `RFID_INIT_CMD_HEX` (sent on connect), `SEQUENCE_STOP_DELAY`.
- **`sequences`** — declarative factory-default sequence list (see [§7](#7-sequence-engine)).
  These are the *fallback* rules; the live rule set comes from the mapping override file if one
  exists (see [§8](#8-rfid-mapping-store)).
- **`features`** — feature flags (see below).
- **`watchdog`** — driver-health timeouts.
- **`mqtt`** *(new, [EVO])* — fleet coordination: `BROKER_IP` (`192.168.2.20`), `BROKER_PORT`
  (`1883`), `CLIENT_ID` (`agv1`/`agv2` — the **wire** id, *not* the `agv-evo-0N` profile name),
  `AGV_INDEX` (`1`/`2`), and the topic roots (`agv/{id}/…`, `store/cmd/{id}/…`). QoS/retain are fixed
  by the MQTT spec, not tuned per profile ([§2b](#2b-fleet-coordination-tasks-evo) and
  `evo-system_mqtt_design.md` §3–4). **Provisional timer defaults (write these so the code runs; tune
  on the deployed network — MQTT doc §5/§7/§11):** `KEEPALIVE=3` s, `ACK_TIMEOUT=1.0` s,
  `ACK_RETRIES=3`, `HEARTBEAT_HZ=1.0`, `HEARTBEAT_MISS=3`.

### Feature flags **[BLDC] [EVO]**

Booleans gate optional subsystems, read **once at boot**:

```
DIO_ENABLED        — DI + DO Modbus module (buttons/lidar/bumper in; pusher/horn out)
CAN_ENABLED        — MGS1600 magnetic-sensor reader / auto-follow (bus itself is always up for motors)
RFID_ENABLED       — RFID reader driver (note: distinct from the live state.rfid_enabled toggle)
LIDAR_STOP_ENABLED — deployment hint only; runtime gate is state.lidar_stop_enabled (defaults ON)
FLEET_MODE [EVO]   — start the MQTT client + heartbeat + mission/traffic/control handling.
                     OFF ⇒ standalone fallback (local panel/dashboard only; no store coordination).
```

The **motor CAN driver is always started**. There is no runtime start/stop of drivers. When
`FLEET_MODE` is **off** the unit behaves exactly like the standalone build; when **on** the store
becomes the primary command source (the local panel/dashboard stays available as a manual fallback,
[§10](#10-web-dashboard--appapppy--apptemplates-bldc)).

---

## 5. Mode state machine — [modes.py](modes.py)

`mode_manager` is the central FSM and the **only** task that owns mode transitions and
spawns/cancels the mode subtasks (`manual_mode`, `auto_mode`). It reads the **physical control
panel** via `latest_di` (buttons wired to the Modbus DIO module).

DI conventions:
- `DI_MODE_SWITCH`: by default HIGH = MANUAL. **Set `MODE_SWITCH_INVERT=1` to flip** (this is the
  case on `agv-evo-01`, so physical HIGH = AUTO).
- `DI_EMERGENCY`: NO contact — `True` = triggered (not safe).
- `DI_START`, `DI_RESET`: momentary NO — act on the rising edge.
- `DI_CONFIRM` **[EVO]**: momentary NO onboard confirm — act on the rising edge ([§5 confirm
  gating](#evo-confirm-gating)).

States: `None → manual | armed | running | emergency`. *(The `reverse` state is **removed** —
[EVO].)*

| Transition | Trigger |
|---|---|
| `None → manual/armed` | startup: MANUAL if selector says manual, else ARMED |
| `* → manual` | mode switch → manual (live) |
| `manual → armed` | mode switch → auto |
| `armed → running` | `DI_START` rising **[EVO]** *(or `cmd/mission` accepted, fleet mode)* **and** tape detected (peeks `sensor_queue`; refused off-tape) |
| `running → armed` | `DI_RESET` rising, **or** `end_cycle_request` (mission complete at home), **or** `sensor_error`, **or** `cmd/control reset` **[EVO]** |
| `* → emergency` | `DI_EMERGENCY` triggered **or** `cmd/control estop` **[EVO]** |
| `emergency → manual/armed` | `DI_RESET` rising (target depends on selector) |

> **[EVO] note:** the `armed → reverse` and `reverse → armed` rows are **deleted** with the reverse
> mode. In fleet mode the AGV boots to **safe idle** (`armed`, no mission) and does **not** resume a
> mission across a restart — the store re-issues `cmd/mission`. `TRAFFIC_HOLD` and the confirm gates
> are **overlays** on `running`, not separate FSM states (see below).

Cross-cutting guards (checked every loop): **[BLDC]**
- **Emergency** cancels the active task and applies a **Category-1 stop** (`_flush_and_brake` →
  `motion.set_cat1_stop`: command zero Target velocity, let the drive decelerate on its
  `QUICKSTOP_DECEL`/Halt ramp, then let the drive's electromagnetic brake hold). Recovery requires
  a `DI_RESET` rising edge. *(There is no 3-second-reset service-restart feature on this branch.)*
- **`system_error`** (critical driver lost — DIO, **or a BLVD-KRD drive FAULT**) cancels the
  active task and Cat-1 stops until cleared. Blocks **all** modes including manual. A drive that
  trips to CiA-402 `FAULT` is surfaced here (see [§9](#9-safety-watchdog-horn--shutdown)).
- **`sensor_error`** (CAN sensor / RFID lost) only forces `running`/`armed` back to
  ARMED; manual stays available.
- **Traffic hold [EVO]** (`state.traffic_hold`, set by `cmd/traffic=stop`): a **non-fault overlay**
  on `running` — Cat-2 hold (zero velocity, **no** electromagnetic brake) so the AGV can resume
  smoothly when `cmd/traffic=go` clears the latch. It does **not** change `current_mode` and is
  distinct from emergency/`system_error`. The AGV emits the `traffic_hold` event **when it has
  actually stopped** (the store treats it as still moving until then); on release it emits
  `traffic_resumed`. See [the traffic-hold overlay](#evo-traffic-hold-overlay).
- **Mapping reload**: on entry to ARMED, if `mapping_reload_pending` is set, the engine hot-swaps
  to the freshly-saved RFID rules (see [§8](#8-rfid-mapping-store)).
- Every halting transition calls `_reset_sequence_state()` → resets `speed_mode` to SLOW, clears
  `sequence_stop`/`pending_sequence`, and calls `engine.cancel_armed()` /
  `cancel_active_sequence()` / `cancel_cooldowns()`.

### `auto_mode` — tape-following PID loop **[BLDC] [EVO]**

The AGV **only ever drives forward** ([EVO] — the physical reverse path is removed): sensor at
front, full speed-mode logic, RFID sequence stops + marker hooks active,
`error_sign = -1 * sensor_orientation`. The logical `direction` flag (`outbound`/`inbound`) is
**not** a drive direction — it is set `outbound` on depart and **flips to `inbound` at the swap on
the last serviced stop** ([EVO], mirrors `evo-system.md` §10.3/§11.2), and is attached to every
`pos` publish.

Each cycle (`~DT`) it:
1. Handles emergency / `sequence_stop` / **traffic_hold [EVO]** (Cat-1/Cat-2 stop & hold) up front.
2. Selects target speed and PID gain set from `speed_mode` (`HIGH`/`SLOW`/`EXTRA_SLOW`), switching
   gains only on change. Target speeds come from the live-tunable `state.auto_*_speed`.
3. **Impact bumper** (`DI_BUMPER`): Cat-1 stop, hold until the bumper clears, wait 2 s, resume.
4. **Lidar zones** (each no-op if the channel is `None`): inner (`DI_LIDAR_STOP`, gated by
   `state.lidar_stop_enabled`) → Cat-1 protective stop; middle (`DI_LIDAR_SLOW`, gated by
   `state.lidar_slow_enabled`) → drop HIGH to SLOW; outer (`DI_LIDAR_OUTER`) → dashboard
   indicator only.
5. Drains `sensor_queue` keep-latest. On **tape lost** → Cat-2 hold (zero velocity, no
   electromagnetic brake) and wait to reacquire; on **CAN timeout**
   (`now - can_last_rx > CAN_TIMEOUT`) → Cat-1 stop, reset PID, wait.
6. Fires **marker hooks** into the SequenceEngine: CAN `left_marker`/`right_marker`.
7. Ramps `current_target_speed` toward target (`ACCEL_RATE × DT`), computes PID → per-wheel rpm,
   clamps each to `±MOTOR_MAX_RPM`, queues the two **motor velocity** commands (signed rpm) onto
   `motor_queue`, records `motion_telemetry` + a plot buffer
   ([_debugging/plotter.py](_debugging/plotter.py) `RunRecorder`).
8. Emits periodic `[DIAG]` lines: measured cycle time, sensor-frames-per-cycle, and motor/do queue
   depths — used to judge whether lowering `DT` is bottlenecked by the controller or the writers.

### `manual_mode` — pendant + web remote

Drains `di_queue` and maps button combinations (`fwd+left`, `rvs+right`, single buttons, idle) to
motion primitives, with a smooth ramp-up fraction (instant stop on release). The **web remote**
(`state.web_manual_command`, set by `POST /api/manual/command`) takes **priority** over physical
buttons and auto-expires after a 500 ms watchdog so a dropped connection stops the AGV. It also
services **`web_pusher_request`** (up/down/clear) by spawning `motion.pusher_*` tasks. Emergency is
owned entirely by mode_manager; manual_mode does not check it.

### Mission FSM (fleet `running`) **[EVO]**

> **Required reading for Phase B:** `evo-system.md` §6/§9/§10.3/§11/§16 is **normative** for the
> mission FSM states, `single_agv` behaviour, traffic arbitration, and safe-idle boot. Implement the
> FSM against it; the summary below is orientation, not the spec.

In fleet mode the existing `running` mode is **elaborated into the EVO mission FSM** that
`evo-system.md` §10.3 defines, driven by an accepted `cmd/mission`
(`loop`, ordered `active_stops[]`, `loading{front,rear}`, `trip_id`):

```
IDLE_HOME → DEPART_HOME → AT_ATTACH(load + confirm) → TRAVEL_1 → AT_STOP_1(swap + confirm)
          → [TRAVEL_2 → AT_STOP_2(swap + confirm)] → RETURN → AT_HOME_UNLOAD(confirm) → IDLE_HOME
```

- The bracketed second stop is skipped on a single-trolley trip. `mission_state` is published on
  every transition (`agv/{id}/state` retained + a `state_change` event).
- **The local mapping store / SequenceEngine still executes each stop** (decision: AGV keeps the
  mapping store — see [§7](#7-sequence-engine)/[§8](#8-rfid-mapping-store)). The mission only tells
  the AGV *which loop* and *which of its mapped stops are active this trip* plus the *loading order*
  for the MP display; the *behaviour at each stop* (where to stop, pusher actuation, slow zones,
  corner speed) comes from the existing RFID-tag rules.
- `at_home` / `end_cycle_request` (existing fields) remain the home-arrival terminator; on mission
  completion the AGV emits `end_cycle` → returns to `armed` and awaits the next `cmd/mission`.
- **`pos` is published on every localizing tag read** (in `rfid_processor`), carrying `tag_id` +
  the logical `direction`, **before** local dispatch — this is the store traffic arbiter's fast
  path. Non-localizing speed/corner tags (reused ids) are handled locally and need not be published.

### Traffic-hold overlay **[EVO]** {#evo-traffic-hold-overlay}

`cmd/traffic` is a **safety overlay** on `running`, not a mode:

- `stop` → set `state.traffic_hold`; `auto_mode` performs a **Cat-2 hold** (zero velocity, no brake)
  so it can resume smoothly. Emit the **`traffic_hold` event only once actually stopped** — the
  store treats the AGV as still moving until that event arrives (never on the command ACK).
- `go` → clear the latch; resume tape-following; emit `traffic_resumed`.
- Distinct from emergency/`system_error`: a traffic hold is **not** a fault, does not need a
  `DI_RESET`, and does not engage the electromagnetic brake.
- Each `cmd/traffic` carries a per-AGV monotonic `seq`; a re-sent/reordered stop is idempotent
  (de-duplicated by `state.last_applied_seq`).

### Confirm gating **[EVO]** {#evo-confirm-gating}

The onboard **confirm button** (`DI_CONFIRM`, rising edge) gates departure at every human-handoff
point — the AGV **will not move** until it is pressed:

1. **AT_ATTACH** after the empties are loaded → depart to first stop.
2. **AT_STOP_n** after the swap (empty removed, full attached) → proceed.
3. **AT_HOME_UNLOAD** after the fulls are removed → park (IDLE_HOME).

Each press emits a `confirm` event (`{stop, location}`). If confirm is not pressed within **100 s**
the AGV raises an **advisory alarm** (web app + `event`) but keeps waiting — it never moves on its
own. The web manual fallback can also satisfy a confirm when operating detached.

### External control (`cmd/control`) **[EVO]**

`pause` / `resume` / `reset` / `estop` map onto existing transitions: `estop` → `emergency`
(Cat-1 stop); `reset` → back to `armed` (clears mission, like `DI_RESET`); `pause`/`resume` →
a commanded Cat-2 hold/resume analogous to traffic-hold. Every command is ACKed
(`accepted`/`rejected(reason)`/`superseded`) and validated by `seq`.

---

## 6. PID controller — [core/pid.py](core/pid.py) + [motion.py](motion.py) **[BLDC]**

`PIDController` steers by driving the **lateral offset** (MGS1600 `left_mm`) to zero. The
controller output is in rpm and feeds CiA-402 Target velocity directly.

- **Process variable:** `pv = left_mm` (mm off tape centre). `error = error_sign * pv`.
- **P**: `kp * e`.
- **I**: accumulates only inside a **deadband** (`|e| < TI_DEADBAND`), anti-windup clamped to
  `±TI_MAX`; term is `kp * (1/TI) * integral`.
- **D**: low-pass filtered derivative, coefficient `alpha = dt / (td/n + dt)`.
- **Output clamp** `±OUTPUT_CLAMP_RPM` applies to the PID steering term.
- **Speed reduction**: a filtered, capped (`SR_CAP * base_rpm`) term that slows *both* wheels when
  error/derivative is large.
- **Output mixing:** `left_rpm = base - sr + output`, `right_rpm = base - sr - output`.
- `update_gains()` switches gain sets live **without** resetting the integrator (smooth
  HIGH↔SLOW); `reset()` zeros all integral/filter state.

> *No curvature feedforward / `nav_in_corner` term exists on this branch* — that was part of the
> removed NAV corner-zone machinery.

`motion.py` converts speed → motor rpm and emits queued motor-velocity / DO commands:
- `mps_to_rpm(v)` = `(v·60 / WHEEL_CIRCUMFERENCE) · GEAR_RATIO` — motor shaft r/min, the final
  unit fed to the drive. A per-side `invert` flag (from `motor_can`) flips the sign for the
  mirror-mounted wheel.
- Drive primitives translate to **signed Target velocity per wheel** (and pusher/horn DO):
  - `set_forward/reverse` → both wheels `+rpm` / `−rpm`; `set_left/set_right` and the diagonal
    combos → asymmetric/opposite-sign per-wheel velocities. *(The negative-velocity `set_reverse`
    capability survives only for the **manual jog** — there is no auto reverse mode [EVO].)*
  - **`update_velocities(left_rpm, right_rpm)`** — the fast path during the manual ramp that only
    re-queues the two motor commands.
  - `idle` → command `0` rpm with the drives left in **Operation Enabled** (Cat-2 hold, no
    electromagnetic brake — the AGV can be pushed by hand).
  - `set_brake` / `set_cat1_stop` → command `0` rpm and request a **CiA-402 Quick stop / Halt**
    (Controlword `0x6040`), so the drive decelerates on `QUICKSTOP_DECEL` (`0x6085`) and then holds
    with its **electromagnetic brake**. Used for emergency, lidar-inner, and bumper stops.
- **Pusher** primitives `pusher_up` (extend) / `pusher_down` (retract) / `pusher_clear` are
  unchanged — still Modbus DO relays, with the mandatory 200 ms relay-deadtime sleep between
  de-energising one direction and energising the other — **do not remove it**.

### The motor driver — `drivers/can_bldc.py` (`CANMotorDriver`) **[BLDC]**

A new actuator driver that owns the shared `canopen.Network` and the two `BaseNode402` drive
nodes. It **adapts** the bring-up reference
[can_bldc/canopen_blv_r1.py](can_bldc/canopen_blv_r1.py) — **do not copy it literally.** The
reference is **single-node** (node 1), uses `channel="COM16"` (Windows dev), **polls SDOs in a
blocking `for` loop**, and builds its own `Network` then `disconnect()`s. The target instead:

- hosts **two** nodes (left=1, right=2), each walked through the 402 state machine independently and
  each Target velocity clamped to `±MOTOR_MAX_RPM`;
- keeps **one always-on** shared `Network` (also carrying the MGS1600 sensor —
  [§2a](#2a-the-shared-can-bus-bldc)), never `disconnect()`ing mid-run;
- applies per-side **`invert`** (sign flip) or the drive's `0x41A4` — never an `if side == "left"`
  branch;
- reads `CHANNEL`/`BITRATE`/accel-decel from `motor_can` (the reference hardcodes `COM16` and `2000`);
- may address objects by **symbolic name** (`node.sdo["Target velocity"].raw`, names verified in the
  EDS) **or by index** (`node.sdo[0x60FF].raw`) — both work;
- runs every blocking SDO/NMT/402 call via `loop.run_in_executor` (the **Golden rule** — SDOs block).

Bring-up sequence (mirrors the reference, applied per node):

1. **Bus + nodes:** `network.connect(interface="slcan", channel=…, bitrate=500000)`; add
   `BaseNode402(node_id, EDS)` for left (1) and right (2).
2. **NMT:** each node `PRE-OPERATIONAL → OPERATIONAL`.
3. **Fault recovery:** if a node is in `FAULT`, call `fault_reset()`.
4. **CiA-402 state machine:** `READY TO SWITCH ON → SWITCHED ON → OPERATION ENABLED`.
5. **Mode + ramps:** `Modes of operation` (`0x6060`) = **3 (Profile Velocity)**; set
   `Profile acceleration` (`0x6083`) / `Profile deceleration` (`0x6084`) from `motor_can`.
6. **Run loop:** drains `motor_queue` and writes **`Target velocity`** (`0x60FF`, INT32,
   ±4,000,000) per node; reads back **`Velocity actual value`** (`0x606C`) and **`Statusword`**
   (`0x6041`) for telemetry and fault detection. Because the canopen SDO calls are blocking, the
   loop body runs via `run_in_executor`.

> CiA-402 reference (from [BLVD-KRD_CANopen_V400.eds](can_bldc/BLVD-KRD_CANopen_V400.eds)):
> Controlword `0x6040`, Statusword `0x6041`, Modes of operation `0x6060`/display `0x6061`,
> Target velocity `0x60FF`, Velocity demand `0x606B`, Velocity actual `0x606C`,
> Profile accel/decel `0x6083`/`0x6084`, Quick-stop decel `0x6085`, Quick-stop option `0x605A`,
> Motor rotation direction `0x41A4`, Gear ratio `0x6091`. *Supported drive modes* (`0x6502`) =
> `0x2D` → Profile Position, **Profile Velocity**, Torque, Homing. Default PDOs already map
> Controlword/Target-velocity (RPDO4 `0x1603`) and Statusword/Velocity-actual (TPDO4 `0x1A03`),
> so a later optimisation can drive the wheels over **PDOs** instead of SDOs for lower latency.

---

## 7. Sequence engine — [core/sequence_engine.py](core/sequence_engine.py)

A **declarative, dict-driven** runner. The engine is fed a list of sequence dicts at construction
and can be **hot-reloaded** at runtime (`reload_sequences`, refused while a sequence is running).
Adding a new *action type* means adding one method and registering it in `__init__` (or via
`register_action`).

Each sequence has a `trigger`, an ordered `actions` list, a `cooldown_s`, an optional
`requires_mode`, and an optional **`requires_at_home`** (gates the two tag-10 sequences against
`state.at_home`). Triggers:

- **`rfid`** — fires immediately when a matching RFID tag is read.
- **`marker`** — fires when a matching tape marker (`left`/`right`) is detected by auto_mode.
- **`rfid_then_marker`** — RFID **arms** an approach (slows to SLOW, records `pending_sequence`);
  the later marker fires execution and skips the already-done approach steps.

Built-in actions: `set_speed`, `wait_marker`, `stop_agv`, `resume`, `sequence_stop` (timed
stop+auto-resume), `wait_seconds`, **`pusher_extend`**, **`pusher_retract`** (each: drive pusher,
hold `duration`, then `pusher_clear`), **`set_at_home`** (sets the home flag), and **`end_cycle`**
(signals mode_manager to return to ARMED — the home-arrival terminator).

Concurrency & safety: at most **one** sequence runs at a time (`_active_sequence` guard);
preconditions are `requires_mode` + `requires_at_home` + cooldown + no-active-sequence.
`cancel_active_sequence()`, `cancel_armed()`, and `cancel_cooldowns()` are called by mode_manager
on transitions to flush stale state. A `wait_marker` timeout raises `SequenceTimeout`, which
auto-resumes.

`status()` returns the active/armed/cooldown snapshot for the dashboard.

---

## 8. RFID mapping store — [core/mapping_store.py](core/mapping_store.py)

The operator-facing way to define RFID→action behaviour **without editing the profile JSON**. The
live rule set is an override file `profiles/{AGV_ID}_sequences.json`; the profile's `sequences[]`
is the read-only factory default and is never modified.

- **Load order** ([main.py](main.py)): if the override file exists, its rules are compiled to
  engine sequences; otherwise the profile `sequences[]` is used.
- **Preset rule types** (validated, max 32 rules): `end_cycle`, `start_cycle`, `timed_pause`,
  `pause_until_tag`, `slow_zone`, `pulse_pusher`. `compile_to_sequences()` expands each preset
  into one or more engine sequence dicts; `decompile_profile_sequences()` reverse-engineers
  profile sequences back into editable presets on first open.
- **Persistence:** atomic tmp-file + `os.replace` write; 5-slot backup rotation
  (`.json.bak.1..5`); append-only audit log (`.audit.jsonl`); export/import bundles.
- **Apply timing:** saving sets `state.mapping_reload_pending`; rules take effect on the next
  **ARMED** entry (`mode_manager` calls `engine.reload_sequences`). `/api/mappings/apply_now`
  forces an immediate reload via `loop.call_soon_threadsafe` (rejected while RUNNING / a sequence
  is active).
- **`at_home` cycle logic:** the two tag-10 sequences (arrival vs. departure) are selected by
  `requires_at_home`. The **start-from-home routine** in mode_manager hardens this: on START with
  RFID enabled, the AGV deterministically holds (`sequence_stop`) and drives the pusher UP for 5 s
  before any motion, independent of whether a tag fires.
- Tags read but matching no rule are recorded in `state.unmapped_rfid_log` and surfaced on the
  dashboard.

### Fleet mapping authority **[EVO]**

In fleet mode the **mapping store stays authoritative for per-stop behaviour** — it is *not*
replaced by the store mission. The division of responsibility is:

- **Store decides the trip:** which loop, which of this AGV's mapped stops are active this trip
  (pairing/visit-order/dispatch), and the front/rear loading order for the MP display. Delivered via
  `cmd/mission`.
- **AGV decides the behaviour at each stop:** the local RFID→action rules (`slow_zone`,
  `pulse_pusher`, `sequence_stop`, corner speed, `set_at_home`, `end_cycle`, the start-from-home
  pusher routine) execute exactly as today.
- **Reconciliation (resolved):** `evo-system.md` §6/§9 describe the store sending *fully ordered
  stops*. **The join is by tag id:** `cmd/mission.stops[].tag` values are the **same RFID tag ids**
  as the AGV's mapping-store rules — both sides draw them from `rfid_mapping.md` as the single source
  of truth. The AGV consumes the mission as the **active-stop set + ordering hint**, then executes
  each stop via its **existing local RFID rule** when that tag is read (the store never ships
  behaviour, only *which* stops are active + the loading order). `start_cycle` aligns with mission
  depart; `end_cycle` with mission completion at home. A mission tag with no local rule still streams
  as `pos` and lands in `unmapped_rfid_log`.
- **Still published:** every tag read also goes out as `pos` (§5); unmapped tags continue to feed
  `unmapped_rfid_log` locally **and** are visible to the store via the `pos` stream.

---

## 9. Safety, watchdog, horn & shutdown

### Safety watchdog — [safety_watchdog.py](safety_watchdog.py) **[BLDC]**
Polls a list of `(driver, timeout_s, auto_only)` tuples every 50 ms. **Two tiers:**
- **Critical** (`auto_only=False`, the DI driver): loss → `system_error`, **immediately commands
  both drives to zero velocity (Quick stop)**, blocks **all** modes (you can't even read the
  E-stop button without DI).
- **Sensor / auto-only** (`auto_only=True`, CAN sensor + RFID): loss → `sensor_error`, blocks auto
  modes only; manual stays operational.

Both clear automatically when the driver recovers. Driver health comes from the
[drivers/base.py](drivers/base.py) `SensorDriver` ABC: `_record_rx()` on every good read,
`get_health() → {ok, last_rx, detail}`.

**Drive faults:** the `CANMotorDriver` watches each BLVD-KRD `Statusword` (`0x6041`). A drive that
trips to CiA-402 `FAULT` (over-current, over-temp, following error, etc.) sets `state.motor_fault`
→ `system_error`, which forces a Cat-1 stop; recovery issues `fault_reset()` and re-walks the 402
state machine back to `OPERATION ENABLED`. (Output drivers are otherwise not in the watchdog list;
the motor drive is the exception because it reports rich status over the bus.)

### Fleet liveness — heartbeat + Last-Will **[EVO]**
Independent of the local `safety_watchdog` (which protects *this* unit), fleet liveness is what lets
the **store** detect a down AGV and enter `single_agv` mode:

- **`heartbeat_publisher`** emits `agv/{id}/heartbeat` (~1 Hz, QoS 0) — its *absence* is the signal.
- The MQTT client registers a **Last-Will** on `agv/{id}/health=offline` (retained, QoS 1) so an
  ungraceful drop is also detected by the broker.
- `state.store_link_ok` tracks broker connectivity for the local monitor. If the broker is
  unreachable the unit keeps running its current mission/standalone behaviour — loss of the store
  link is **not** itself a motion fault.
- **Traffic hold is not a fault:** it is a commanded overlay ([§5](#evo-traffic-hold-overlay)) and
  does **not** enter `system_error`/`emergency` or engage the brake.

### Horn controller — [horn_controller.py](horn_controller.py)
A standalone task driving two DO channels: `regular_horn` (steady ON while in the auto `running`
mode) and `alarm_horn` (steady ON *instead* when an alarm is active during auto: `system_error`,
`sensor_error`, `bumper_active`, lidar-inner DI high, or tape lost). Both OFF outside auto modes.
Writes to `do_queue` only on change.

### Shutdown — [main.py](main.py) **[BLDC]**
`SIGTERM`/`SIGINT` cancels all tasks; on `CancelledError` the controller stops the web server,
then leaves the AGV safe by **commanding both drives to zero velocity, requesting a Quick stop,
and transitioning the CiA-402 state machine down to `SWITCHED ON`** (motors disabled, brake
engaged), and **zeroing all DO coils** (pusher/horn). This bypasses the queues and talks to the
CANopen network / Modbus DIO directly.

### Logging — [logger.py](logger.py)
`setup_logging()` installs a rotating file handler (DEBUG+, 10 MB × 5 in `logs/`) and a console
handler (INFO+ by default; pass `DEBUG` for PID tuning). Third-party noise (werkzeug, pymodbus,
can, **canopen**) is silenced. *(This is a plain blocking config — there is no `QueueListener` /
in-memory ring buffer here.)* Operator-visible events go to `state.event_log` and the `/errors`
page instead. Levels: DEBUG = per-cycle PID/sensor; INFO = connections/mode transitions/sequence
events; WARNING = recoverable (tape loss, CAN timeout, reconnect, unmapped tag); ERROR = comms
failures / **drive faults**; CRITICAL = emergency stop.

---

## 10. Web dashboard — [app/app.py](app/app.py) + [app/templates/](app/templates/) **[BLDC] [EVO]**

Flask in a daemon thread, served at `http://<LOCAL_IP>:5000`. It shares the `AMRState` object
directly and holds references to the `SequenceEngine` and `config`. Top nav:
**HOME · MANUAL · IO · PARAMS · MAPPINGS · ERRORS**.

> **[EVO] role change:** with the store app as the primary control surface, this local app becomes
> **monitor + manual fallback**. The store owns dispatch (`cmd/mission`), traffic, and
> pause/reset/estop; the local Manual page remains usable for **jog/maintenance when detached or
> offline** (and can satisfy a confirm). It should not issue dispatch that competes with the store
> while `FLEET_MODE` is on and `store_link_ok`.

| Page | Route | Purpose |
|---|---|---|
| **Home** | `/` → `/api/state` | Live mode, emergency/error, speed mode, sensor (offset, markers), motion telemetry (per-wheel commanded rpm + PID terms, **plus CiA-402 actual rpm / drive status**), sequence-engine status, last/unmapped RFID, and safety indicators (bumper, lidar outer/slow/stop). **[EVO]** also shows `mission`/`mission_state`, logical `direction`, `traffic_hold`, `confirm_pending`, and `store_link_ok`. |
| **Manual** | `/manual` → `/api/manual/command`, `/api/manual/pusher` | On-screen jog remote + pusher up/down/clear; only in `manual` mode; 500 ms watchdog. **[EVO]** manual = the offline/maintenance fallback. |
| **IO** | `/io` → `/api/io` | Live DI/DO bit monitor with human labels derived from the profile mapping (**DO labels now cover only pusher + horn; the old motor dir/brake coils are gone**). **[EVO]** DI labels include `DI_CONFIRM`. |
| **Params** | `/params` → `/api/params`*(profile dump)*, `/api/tuning`, `/api/tuning/toggle`, `/api/tuning/speeds` | Read-only profile view; **live toggles** (lidar stop, lidar slow, RFID dispatch) and **live auto-speed editing** (HIGH ≥ SLOW ≥ EXTRA_SLOW, bounded) |
| **Mappings** | `/mappings` → `/api/mappings[...]` | RFID→action rule editor: get/save, apply-now, history (audit), backups/restore, export/import, unmapped-tag list |
| **Errors** | `/errors` → `/api/errors`, `/api/errors/clear` | The `event_log`; clear button |
| — | `/api/wifi` | WiFi SSID/signal via `nmcli` |

> **[EVO] removed:** the `/api/reverse_auto` endpoint and the reverse-tape-following control are
> deleted with the reverse mode.

---

## Hardware-free develop & validate **[BLDC] [EVO]** {#hardware-free-develop--validate-bldc-evo}

The repo is developed on Windows, but the vehicle hardware (Modbus DIO, CANable2 + BLVD-KRD drives,
RFID reader) exists only on the AGV. Each phase therefore has a **static / offline checkpoint
reachable on the dev box** before any on-vehicle test:

- **Static acceptance (greppable):** after Phase A, `rg -i 'ao_queue|AO_|voltage_to_rpm|rpm_to_voltage'`
  finds nothing in live code (only history/comments); after Phase B, no `reverse` state /
  `/api/reverse_auto` / `reverse_auto_request` remains.
- **Import smoke:** `AGV_ID=agv-evo-01 python -c "import main"` succeeds; `config` loads the EVO
  profile and the new `motor_can` / `mqtt` blocks with no `KeyError`.
- **CANopen offline check (no adapter):** `net = canopen.Network(); net.connect(interface="virtual");
  net.add_node(canopen.BaseNode402(1, EDS)); net.add_node(canopen.BaseNode402(2, EDS))` — proves the
  EDS parses, the object dictionary resolves the cited indices (`0x6040/41/60/6C/FF/83/84/85`), and
  both nodes wire up. Exercise the `motion` rpm helpers and the `motor_queue` → driver path against
  this virtual bus.
- **MQTT offline check (no store):** run a local Mosquitto (or paho loopback); verify connect + LWT,
  `store/cmd/{id}/#` subscribe, `seq` de-dup, ACK emission, and the `mqtt_out_queue` drain — without
  the real store controller.
- **On-vehicle only:** tape-following, real drive-fault recovery, lidar/bumper stops, and end-to-end
  store dispatch are validated on the AGV. The static tier gates *getting there*; it does not replace
  it.

---

## Repository map & conventions

### Layout **[BLDC] [EVO]**
```
main.py                 entry point, driver wiring, mapping load, shutdown
config.py               profile loader (no fail-fast validation on this branch)
state.py                AMRState + typed domains + property shims (+ FleetState [EVO])
modes.py                mode_manager FSM, auto_mode (forward only), manual_mode, EVO mission FSM
motion.py               kinematics + drive primitives + pusher (reads motor_can / PUSHER_CHANNELS)
horn_controller.py      regular/alarm horn task
safety_watchdog.py      two-tier sensor-driver health monitor
rfid_processor.py       rfid_queue → SequenceEngine dispatcher (+ pos publish [EVO])
logger.py               rotating file + console logging
core/pid.py             PIDController (output in rpm)
core/sequence_engine.py declarative sequence runner (hot-reloadable)
core/mapping_store.py   RFID mapping persistence + compile/validate/decompile
core/mission.py         [EVO] NEW — mission model + EVO mission-FSM helpers (or fold into modes.py)
drivers/base.py         SensorDriver / ActuatorDriver ABCs
drivers/modbus_di.py    DIReader   (Modbus discrete inputs; applies DI_FLIPPED)
drivers/modbus_do.py    DOWriter   (Modbus coils; consumes do_queue — pusher/horn only)
drivers/can_bldc.py     CANMotorDriver (BLVD-KRD CiA-402 over CANopen; consumes motor_queue)
drivers/can_mls.py      CANReader  (SICK MLS magnetic line sensor, CANopen TPDO1; shares the slcan bus) [SENSOR]
drivers/rfid_tcp.py     RFIDReader (raw TCP hex stream; parses 4-char hex tags)
drivers/mqtt_client.py  [EVO] NEW — paho-mqtt client: LWT, sub cmd/*, drain mqtt_out_queue, ACK/seq
evo_topics.py           [EVO] COPY of store-controller/evo_topics.py (shared contract — do not re-author)
can_bldc/canopen_blv_r1.py        reference bring-up script (COPY from repo root into this repo first)
can_bldc/BLVD-KRD_CANopen_V400.eds CiA-402 object dictionary (COPY in; loaded at runtime)
profiles/agv-evo-01.json the active profile (EVO unit 01 — formerly agv_tn / "AGV B / TN")
profiles/agv-evo-02.json [EVO] second fleet unit (agv_index, fleet IP, mqtt block)
profiles/agv-evo-01_sequences.json    RFID mapping override (created by the UI)
profiles/agv-evo-01_sequences.audit.jsonl change log (appended by the UI)
app/app.py              Flask dashboard + all API endpoints
app/templates/*.html    dashboard pages (index, manual, io_monitor, params, mappings, errors)
docs/                   commissioning doc generator + TN training material
_debugging/*.py         standalone hardware test/monitor scripts + plotter RunRecorder
_motion_analysis/       auto-mode PID run CSV/PNG output
_obsolete/io_hardware.py legacy driver re-export shim (don't write new code against it)
_future_implementation_plans/  C++/rclpy/AGV-B→A migration design notes (not code)
```

> **[EVO] companion docs:** `evo-system.md` (fleet system design — loops, dispatch, traffic
> junctions) and `evo-system_mqtt_design.md` (topic tree, QoS/retain, ACK/seq, latency budget).
> `rfid_mapping.md` is the shared tag inventory both the store and this unit must agree on.

### Key design rules **[BLDC]**
1. **Never block the event loop** — the CANopen master (SDOs, NMT/402 transitions, bus setup) and
   any synchronous CAN work go through `run_in_executor`; Modbus and RFID use async/non-blocking
   APIs.
2. **All I/O is queue-based.** Inputs are keep-latest queues; outputs (`motor_queue`/`do_queue`)
   are FIFO and must be **flushed** on mode transitions to avoid asserting stale commands.
3. **All motion goes through `motion.py`** (which queues motor-velocity / `do` writes) — never
   command the drives or Modbus coils directly except in `shutdown()`.
4. **`mode_manager` owns all mode transitions**; `auto_mode`/`manual_mode` are passive subtasks it
   spawns and cancels.
5. **`mode_manager` and `safety_watchdog` are the only tasks that halt motion unilaterally.**
6. **One sequence at a time.** Live RFID behaviour comes from the mapping override file; the
   profile `sequences[]` is the read-only factory default.
7. **Channel/IO mapping is never hardcoded** — always read from `config` / `motor_can` /
   `PUSHER_CHANNELS` / `HORN_CHANNELS`. Wheel-side direction inversion lives in `motor_can.invert`
   (or the drive's `0x41A4`), never in `if side == "left"` branches.
8. **`DI_FLIPPED` inverts at read time** in DIReader — all downstream code sees logical values.
   `MODE_SWITCH_INVERT` similarly flips the manual/auto selector polarity in mode_manager.
9. **Optional hardware degrades gracefully** — lidar zones, bumper, pusher, and horns are all
   `None`-guarded; a profile that omits them simply disables that behaviour. The **motor CAN
   driver is mandatory**.
10. **Speed is r/min end to end** — `mps_to_rpm` produces the value written to CiA-402 *Target
    velocity*. Stops prefer the drive's CiA-402 Quick-stop / electromagnetic brake over any
    external coil.
11. **MQTT never touches hardware [EVO].** The MQTT client only reads/writes `AMRState` + queues; it
    must not command drives/coils directly. Commands are idempotent by per-AGV monotonic `seq`;
    **commands are never retained**; a `traffic_hold`/`state_change` **event** — not the ACK — is the
    proof of physical effect.

### What changes vs. what was never here **[BLDC] [EVO]**

**Present in the cloned code → REMOVE / REPLACE during migration** (do not preserve these in the
target; full per-file delta in [§0.3](#03-phase-a--motor-drive-swap-analog--can-bldc)):
- the **Modbus AO speed DAC** (`drivers/modbus_ao.py` `AOWriter`, `ao_queue`, `AO_IP`,
  `AO_BASE`/`NUM_AO`, `V_RANGE`/`DAC_RES`, `ao_max_voltage`) and the `rpm↔voltage` model — **Phase A**;
- the **DO motor direction/brake coils** (`do_fwd`/`do_rev`/`do_brake` in `motor_channels`) — **Phase A**
  (speed, direction, braking move onto CANopen);
- the **physical reverse auto mode** (`reverse` state, `auto_mode(direction="reverse")`,
  `reverse_auto_request`, `/api/reverse_auto`, horn `_AUTO_MODES` reverse entry) — **Phase B**.

**Genuinely never in this codebase** (the stale `README.md`/`project_overview.md` mention some of
these — ignore them): the **SLMP / Mitsubishi PLC** layer and `pymcprotocol`, the wheel-speed
**calibration mode** (`calibration.py`), the separate **CANopen friction-wheel encoder**
(`drivers/can_encoder.py` — unneeded; the BLV-R reports its own velocity), a **DriverManager / live
driver-toggling**, the **latest-wins `ao_setpoints` setpoint tables** (this code uses FIFO queues),
the **curvature feedforward**, the config `_validate()` fail-fast, and the **hold-RESET-3 s
service-restart** / `/api/restart` path.

---

## 11. Phase B — EVO fleet integration checklist **[EVO]**

Ordered work to take the (post-Phase-A) standalone controller to a fleet edge node — see
[§0.2](#02-migration-overview--two-phases), `evo-system.md`, and `evo-system_mqtt_design.md`.
**Prerequisite: Phase A ([§0.3](#03-phase-a--motor-drive-swap-analog--can-bldc)) is complete** —
the wheels already run over CANopen.

1. **Networking & identity** — move the fleet NIC to `192.168.2.22`/`.24` (retire `.100`); keep the
   `192.168.3.x` field NIC; rename `agv_tn.json` → `profiles/agv-evo-01.json` and add
   `profiles/agv-evo-02.json`, each with `agv_index`, fleet IP, `mqtt.CLIENT_ID` (`agv1`/`agv2`), and
   the `mqtt` block.
2. **MQTT client** — add `paho-mqtt` and `drivers/mqtt_client.py`; **copy
   `store-controller/evo_topics.py` verbatim** into the controller (it is the shared store↔AGV
   contract — do not re-author it). Connect to the store broker with the stable **wire** client id
   (`agv1`/`agv2`) + Last-Will on `agv/{id}/health`; subscribe `store/cmd/{id}/#`. Add
   `mqtt_out_queue` + the MQTT-thread drain.
3. **State & config** — add `FleetState` fields; add the `mqtt` profile block + `FLEET_MODE` flag +
   `DI_CONFIRM`.
4. **Publishers** — `pos` (every localizing tag read, with `direction`) folded into
   `rfid_processor`; `heartbeat_publisher`; retained `state`; `event`/`ack` at the right transitions.
5. **Command handlers** — `cmd/mission` → mission FSM; `cmd/traffic` → traffic-hold overlay;
   `cmd/control` → pause/resume/reset/estop; all validated by `seq`, all ACKed.
6. **Mode FSM** — elaborate `running` into the mission FSM; **remove reverse**; add the logical
   `direction` flag and its flip at the last-stop swap.
7. **Confirm gating** — `DI_CONFIRM` gates at attach/each stop/home; `confirm` events; 100 s advisory
   alarm.
8. **Liveness & safety** — heartbeat/LWT for store `single_agv`; ensure traffic hold is **not** a
   fault; boot to safe idle, no mission resume.
9. **Dashboard** — convert local app to monitor + manual fallback; add fleet panels; delete the
   reverse page/endpoint.
10. **Commission tag lead distances** — per junction, per the MQTT design §10 latency budget (or set
    an approach speed cap); align this unit's mapping-store tag ids with `rfid_mapping.md`.
