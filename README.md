# AGV KIM2A Controller (EVO)

Python `asyncio` controller for a differential-drive, magnetic-tape-following AGV. It drives
the two wheels over **CANopen / CiA-402** (Oriental Motor BLV-R BLDC + BLVD-KRD drivers),
reads a magnetic guide sensor and a station-RFID reader, handles buttons / lidar / bumper and
the pusher (towing-pin) actuator over **Modbus TCP**, and serves a **Flask** web dashboard
from a parallel daemon thread.

> **Status:** this is the post-motor-swap build — wheels run over CANopen (was an analog
> Modbus AO speed DAC + DO direction/brake coils). The fleet/MQTT ("EVO") integration
> described in `_migration_plan_and_docs/` is **not yet in the code**; this unit currently
> runs **standalone**.

---

## Requirements

**Python:** 3.11+ on the vehicle (developed on 3.10+).

**Hardware:**
| Device | Protocol | Purpose |
|---|---|---|
| Modbus DIO module | Modbus TCP | DI: buttons, mode switch, lidar zones, impact bumper · DO: pusher relays, horns |
| 2× BLVD-KRD wheel drives | CANopen / CiA-402 (slcan via CANable2 USB) | Wheel motion — signed Target velocity per wheel |
| SICK MLS (MLSE) magnetic line sensor | CANopen TPDO1 (shares the slcan bus) | Tape following — line center point in mm |
| RFID reader | Raw TCP socket | Station/marker tag detection |

> The wheel drives and the SICK MLS **share one CANable2 adapter** (one `canopen.Network`,
> 500 kbit/s). The MLS publishes its line position on **TPDO1** (COB-ID `0x180 + node id`,
> e.g. 0x18A); the controller reads it by subscribing to that COB-ID on the shared bus rather
> than opening a second adapter. See [drivers/can_bldc.py](drivers/can_bldc.py) and
> [drivers/can_mls.py](drivers/can_mls.py).
>
> **MLS prerequisite:** the sensor ships at 125 kbit/s and must be configured **once** (SICK MLS
> config tool / LSS) to its node id and **500 kbit/s** before joining the shared bus — the
> controller does not set the sensor bitrate.

**Python packages** (`pip install -r requirements.txt`): `pymodbus>=3.6`,
`python-can>=4.3`, `canopen>=2.0`, `pyserial>=3.5`, `Flask>=3.0`, `matplotlib>=3.8`.

---

## Configuration

All tuneable parameters live in a **profile JSON** under `profiles/`, selected via the
`AGV_ID` environment variable. The only profile shipped today is `agv-evo-01.json`
(the second unit would be `agv-evo-02.json`).

### Selecting a profile

```bash
AGV_ID=agv-evo-01 python3 main.py
```

If `AGV_ID` is unset it defaults to `agv-evo-01`. The loader reads `profiles/{AGV_ID}.json`
(falling back to `parameters.json` if present, for backwards compatibility). To make the
variable permanent, see [_setup_guide/01_agv_id_env_var.md](_setup_guide/01_agv_id_env_var.md).

### Profile sections

| Section | Key parameters |
|---|---|
| `networking` | `DIO_IP`, `RFID_IP/PORT`, `MODBUS_PORT`, `DEVICE_ID`, `LOCAL_IP` |
| `io_mapping` | DI bit indices (emergency, mode switch, start/reset, fwd/rev/left/right, lidar, bumper); `DI_FLIPPED` inverts all DI at read time; `MODE_SWITCH_INVERT` |
| `motor_can` | CANopen drive setup: per-side `{node_id, invert}`, `CHANNEL` (slcan device, `null` = auto-detect), `BITRATE`, `EDS`, `PROFILE_ACCEL/DECEL`, `QUICKSTOP_DECEL`, `MOTOR_MAX_RPM` |
| `pusher_channels` | DO indices for the double-acting linear actuator (`extend`, `retract`) |
| `horn_channels` | DO indices for `regular_horn` and `alarm_horn` |
| `kinematics` | `WHEEL_DIAMETER`, `GEAR_RATIO` (→ `mps_to_rpm`) |
| `speeds` | manual/auto HIGH·SLOW·EXTRA_SLOW targets (m/s), `ACCEL_RATE`, `MANUAL_ACCEL_RATE` |
| `pid_tuning` | `KP/TD/N` (HIGH), `KP_SLOW/TD_SLOW/N_SLOW` (SLOW), `TI`, `DT`, `OUTPUT_CLAMP_RPM`, speed-reduction coefficients |
| `can_sensor` | SICK MLS: `SENSOR_COB_ID` (0x180+node), `CAN_NODE_ID`, `STEERING_LCP` (which line center point to follow, default LCP2), `LCP_INVALID`, `CAN_TIMEOUT` |
| `rfid` | `RFID_INIT_CMD_HEX`, `SEQUENCE_STOP_DELAY` |
| `sequences` | Factory-default RFID→action rules (see RFID Mappings) |
| `features` | `DIO_ENABLED`, `MOTOR_CAN_ENABLED`, `CAN_ENABLED`, `RFID_ENABLED`, `LIDAR_STOP_ENABLED` |
| `watchdog` | `DI_TIMEOUT_S`, `CAN_TIMEOUT_S`, `RFID_TIMEOUT_S` |

Top-level `sensor_orientation` (`1` normal / `-1` rear-facing) flips the PID error sign;
`agv-evo-01` uses `-1`.

### Feature flags / running with no hardware

Every hardware subsystem can be disabled independently in `features`, so the controller boots
cleanly on a PC with **nothing connected** (no connect errors) and the dashboard still comes
up:

| Flag | Gates |
|---|---|
| `DIO_ENABLED` | Modbus DI reader + DO writer (buttons/lidar/bumper · pusher/horn) |
| `MOTOR_CAN_ENABLED` | BLVD-KRD wheel drives **and** the shared CAN bus |
| `CAN_ENABLED` | SICK MLS sensor / auto-follow — shares the bus, so it needs `MOTOR_CAN_ENABLED` on too |
| `RFID_ENABLED` | RFID TCP reader |

A subsystem whose flag is on but whose device is missing logs and **retries** rather than
crashing the process. (`LIDAR_STOP_ENABLED` is a deployment hint only; the live gate is the
dashboard toggle, default ON.)

---

## Running

```bash
cd agv-kim2a-controller
AGV_ID=agv-evo-01 python3 main.py
```

The controller starts all asyncio tasks and the Flask dashboard together. Send `SIGINT`
(Ctrl+C) or `SIGTERM` to shut down cleanly — on exit the drives are commanded to a safe stop
(0 rpm + CiA-402 Quick stop, state machine down to `SWITCHED ON`) and all DO coils are zeroed.

For unattended deployment (auto-start service + on-screen dashboard), see
[_setup_guide/](_setup_guide/).

**Startup sequence:**
1. Profile loaded from `profiles/{AGV_ID}.json`
2. RFID mapping override loaded from `profiles/{AGV_ID}_sequences.json` (if present); else profile `sequences[]`
3. Hardware drivers instantiated (feature-flag gated); `CANMotorDriver` brings up the shared CAN bus
4. Flask dashboard started on port 5000
5. Mode manager waits for DI, then enters MANUAL or ARMED per the mode switch

---

## Web Dashboard

Access from any device on the network: `http://{LOCAL_IP}:5000` (Flask binds `0.0.0.0:5000`).

| Page | URL | Description |
|---|---|---|
| Home | `/` | Live status: mode, sensor offset/markers, motion telemetry (commanded per-wheel rpm + PID terms), sequence engine, safety indicators |
| Manual | `/manual` | Web jogging + pusher up/down/clear (manual mode only; 500 ms watchdog) |
| IO Monitor | `/io` | Real-time DI/DO state with profile-derived labels (DO = pusher + horn) |
| Parameters | `/params` | Read-only profile view + runtime tuning toggles and auto speeds |
| Mappings | `/mappings` | RFID→action rule editor (see below) |
| Errors | `/errors` | Timestamped event log |

### Runtime tuning (Parameters page)

Without restarting, operators can toggle **Lidar inner stop** / **Lidar middle slow**,
**RFID dispatch**, and edit the **auto speeds** (HIGH ≥ SLOW ≥ EXTRA_SLOW, bounded). These
change behaviour only — they do not start/stop drivers.

---

## RFID Mappings

Station tags are matched against a rule list managed via `/mappings` and saved to
`profiles/{AGV_ID}_sequences.json` — the profile's `sequences[]` is never modified.

| Type | Trigger | Behaviour |
|---|---|---|
| **End cycle** | Single tag | Stop → pusher retract → `at_home=true` → return to ARMED |
| **Start cycle** | Single tag (requires `at_home`) | Stop → pusher extend → `at_home=false` → resume HIGH |
| **Slow zone** | Paired tags | Start tag → SLOW; end tag → HIGH |
| **Timed pause** | Single tag | Stop N seconds, then auto-resume |
| **Pause until tag** | Paired tags | Stop at start tag; resume on resume tag |
| **Pulse pusher** | Single tag | Stop → extend/retract pusher N seconds → resume |

**Apply timing:** *Confirm* saves to disk and flags a reload; rules take effect on the next
**ARMED** entry. *Apply now* forces an immediate reload (rejected while a sequence is running).
**Audit/rollback:** each save rotates the previous file to `.bak.1..5`; History/Restore in the
UI. **Export/Import** moves the mapping file between units. On first open with no override file,
the profile's factory sequences are imported as editable presets (save to make them live).

---

## Mode State Machine

```
                 ┌──────────────────────────┐
                 │        EMERGENCY          │  (DI_EMERGENCY active → Cat-1 stop)
                 └──┬────────────────────────┘
                    │ RESET (target depends on mode switch)
          ┌─────────▼──────────┐
          │       ARMED        │◄─── end_cycle / reset / sensor error
          └─────────┬──────────┘
              START │ + tape detected            REVERSE ◄── reverse_auto_request (+ tape)
          ┌─────────▼──────────┐              (auto tape-follow, both wheels reverse)
          │      RUNNING       │
          │ (auto tape-follow  │
          │   + sequences)     │
          └────────────────────┘

MANUAL ◄──► ARMED/RUNNING   (mode switch live; manual jog incl. reverse)
```

**DI conventions:** `DI_EMERGENCY` NO contact (`True` = triggered); `DI_MODE_SWITCH` HIGH =
MANUAL by default (`MODE_SWITCH_INVERT=1` flips — set on `agv-evo-01`); `DI_START` / `DI_RESET`
momentary NO, rising-edge.

**Stops:** Cat-1 (emergency / lidar-inner / bumper) = 0 rpm + CiA-402 Quick stop, drive holds
with its electromagnetic brake. Cat-2 (tape lost / idle hold) = 0 rpm, drives stay Operation
Enabled (no brake). A BLVD-KRD drive that trips to CiA-402 `FAULT` is surfaced as
`system_error` via the watchdog and the driver attempts `fault_reset` + re-enable.

---

## Architecture

```
main.py  ─── asyncio.gather ──────────────────────────────────────────────
              │
              ├─ CANMotorDriver.run()   motor_queue → CiA-402 Target velocity (both wheels)
              │                         owns the shared canopen.Network; reads back status/vel
              ├─ DIReader.run()         Modbus TCP DI poll (50 ms) → latest_di + di_queue
              ├─ DOWriter.run()         do_queue → Modbus TCP coils (pusher, horn)
              ├─ CANReader.run()        SICK MLS TPDO1 off the shared bus → sensor_queue
              ├─ RFIDReader.run()       TCP socket → rfid_queue
              ├─ safety_watchdog()      driver health (50 ms) + drive-FAULT tier
              ├─ rfid_processor()       rfid_queue → SequenceEngine
              ├─ horn_controller()      regular / alarm horn DO outputs
              └─ mode_manager()         central FSM; spawns:
                    ├─ manual_mode()    pendant + web jogging (signed-rpm ramp)
                    └─ auto_mode()      PID tape-follow → signed per-wheel rpm + sequence hooks

Flask daemon thread:  app/app.py       dashboard + API endpoints
Shared state:         AMRState         queues + typed domains (GIL-safe)
```

Golden rule: never block the event loop. The CANopen master (SDO / NMT / 402 transitions /
bus setup) is synchronous, so it runs via `loop.run_in_executor`; Modbus and RFID use the
async / non-blocking APIs directly.

---

## Project Structure

```
agv-kim2a-controller/
├── main.py                     Entry point — driver wiring, mapping load, shutdown
├── config.py                   Profile loader — reads profiles/{AGV_ID}.json
├── state.py                    AMRState + typed domain objects (incl. drive telemetry)
├── modes.py                    auto_mode, manual_mode, mode_manager
├── motion.py                   Kinematics + signed-rpm drive primitives + pusher
├── rfid_processor.py           RFID tag dispatcher
├── horn_controller.py          Regular / alarm horn task
├── safety_watchdog.py          Driver health monitor (+ drive-FAULT tier)
│
├── profiles/
│   └── agv-evo-01.json         Active profile (EVO unit 01; ex AGV B / TN)
│       (agv-evo-01_sequences.json / .audit.jsonl created by the mapping UI)
│
├── core/
│   ├── sequence_engine.py      Declarative RFID/marker sequence runner
│   ├── mapping_store.py        RFID mapping persistence + compile/validate
│   └── pid.py                  PID controller (output in rpm) with filtered derivative
│
├── drivers/
│   ├── base.py                 SensorDriver / ActuatorDriver ABCs
│   ├── modbus_di.py            DI reader (Modbus TCP)
│   ├── modbus_do.py            DO writer (Modbus TCP — pusher/horn)
│   ├── can_bldc.py             CANMotorDriver (BLVD-KRD CiA-402; owns the shared CAN bus)
│   ├── can_mls.py              CANReader (SICK MLS sensor TPDO1; attaches to the shared bus)
│   └── rfid_tcp.py             RFID TCP reader
│
├── can_bldc/
│   ├── BLVD-KRD_CANopen_V400.eds   CiA-402 object dictionary (loaded at runtime)
│   └── canopen_blv_r1.py           single-node bring-up reference script
│
├── can_magnetic_sensor/
│   ├── MLS_v7.eds                  SICK MLS object dictionary (reference)
│   └── read_mls.py                 minimal TPDO1 reader (reference)
│
├── app/
│   ├── app.py                  Flask server + all API endpoints
│   └── templates/              index · manual · io_monitor · params · mappings · errors
│
├── _setup_guide/               Deployment: AGV_ID env, systemd service, XFCE kiosk
├── _migration_plan_and_docs/   Target spec + EVO/MQTT fleet plan (not yet in code)
├── _debugging/                 Standalone hardware test/monitor scripts + plotter
├── logs/                       Rotating log files
└── requirements.txt
```

---

## Logging

Rotating files in `logs/` (10 MB × 5) plus console.

| Level | Used for |
|---|---|
| `DEBUG` | PID loop values, raw sensor frames (console-suppressed by default) |
| `INFO` | Connections, mode transitions, sequence events |
| `WARNING` | Tape loss, CAN timeout, driver reconnects, unmapped tags |
| `ERROR` | Comms failures, drive faults, unhandled exceptions |
| `CRITICAL` | Emergency stop |
