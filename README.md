# AGV KIM2A Controller

Python asyncio controller for a differential-drive tape-following AGV. Controls hardware over Modbus TCP (DI/DO/AO), CAN bus (magnetic sensor), TCP socket (RFID), and optionally SLMP/MC Protocol (Mitsubishi PLC). A Flask web dashboard runs in a parallel daemon thread.

---

## Requirements

**Python:** 3.11+

**Hardware:**
| Device | Protocol | Purpose |
|---|---|---|
| Modbus DIO module | Modbus TCP | 16 DI (buttons, lidar, bumper) + 16 DO (motor relays, horn, pusher) |
| Modbus AO module | Modbus TCP | 2 AO channels → motor speed DAC (0–10V) |
| MGS1600 magnetic guide sensor | CANopen via CANable2 USB | Tape following (lateral error in mm) |
| RFID reader | Raw TCP socket | Station tag detection |
| Mitsubishi FX5U PLC *(optional)* | SLMP / MC Protocol Type3E | Sequence handshake (disabled on AGV B) |

**Python packages:**

```
pip install -r requirements.txt
```

Key dependencies: `pymodbus>=3.6`, `python-can>=4.3`, `Flask>=3.0`, `pymcprotocol>=0.3`.

---

## Configuration

All tuneable parameters live in a **profile JSON** under `profiles/`. The active profile is selected via the `AGV_ID` environment variable.

### Selecting a profile

```bash
AGV_ID=agv_tn       python3 main.py   # AGV B (TN) — pusher + lidar, no PLC
AGV_ID=agv1_kim     python3 main.py   # AGV A — with PLC/SLMP
AGV_ID=agv2_kim     python3 main.py   # AGV 2
```

If `AGV_ID` is not set, it defaults to `agv1_kim`. The loader looks for `profiles/{AGV_ID}.json`; if not found it falls back to `parameters.json` for backwards compatibility.

### Profile sections

| Section | Key parameters |
|---|---|
| `networking` | `DIO_IP`, `AO_IP`, `RFID_IP/PORT`, `SLMP_IP/PORT`, `MODBUS_PORT`, `DEVICE_ID` |
| `io_mapping` | Channel indices for every DI/DO signal; `DI_FLIPPED` inverts all DI bits at read time |
| `motor_channels` | DO/AO channel indices per wheel (`do_fwd`, `do_rev`, `do_brake`, `ao_speed`) |
| `pusher_channels` | DO indices for the double-acting linear actuator (`extend`, `retract`) |
| `horn_channels` | DO indices for `regular_horn` and `alarm_horn` |
| `hardware` | `V_RANGE` (max AO voltage), `DAC_RES` (DAC bits) |
| `kinematics` | `WHEEL_DIAMETER`, `GEAR_RATIO` |
| `speeds` | `AUTO_TARGET_HIGH_SPEED`, `AUTO_TARGET_SLOW_SPEED`, `AUTO_TARGET_EXTRA_SLOW_SPEED`, `ACCEL_RATE`, `MANUAL_ACCEL_RATE` |
| `pid_tuning` | `KP/TD/N` (HIGH gains), `KP_SLOW/TD_SLOW/N_SLOW` (SLOW gains), `TI`, `DT`, speed-reduction coefficients |
| `can_sensor` | `SENSOR_COB_ID`, flag bitmasks, `CAN_NODE_ID` |
| `rfid` | `RFID_INIT_CMD_HEX`, `SEQUENCE_STOP_DELAY` |
| `sequences` | Factory-default RFID→action rules (see RFID Mappings) |
| `features` | `DIO_ENABLED`, `CAN_ENABLED`, `RFID_ENABLED`, `SLMP_ENABLED`, `LIDAR_STOP_ENABLED` |
| `watchdog` | `DI_TIMEOUT_S`, `CAN_TIMEOUT_S`, `RFID_TIMEOUT_S` |

### AGV B (TN) extras

Two profile-level keys are specific to AGV B:

```json
"ao_max_voltage": 5.0,
"sensor_orientation": -1
```

`ao_max_voltage` caps the speed DAC output. `sensor_orientation` flips the PID error sign when the sensor is mounted rear-facing (`-1`).

---

## Running

```bash
cd agv-kim2a-controller
AGV_ID=agv_tn python3 main.py
```

The controller starts all asyncio tasks and the Flask dashboard simultaneously. Send `SIGINT` (Ctrl+C) or `SIGTERM` to shut down cleanly — all DO and AO outputs are zeroed before exit.

**Startup sequence:**
1. Profile loaded from `profiles/{AGV_ID}.json`
2. RFID mapping override loaded from `profiles/{AGV_ID}_sequences.json` (if present); falls back to profile `sequences[]`
3. Hardware drivers initialised (feature-flag gated)
4. Flask dashboard started on port 5000
5. Mode manager waits for DI to become available, then enters MANUAL or ARMED depending on the mode switch

---

## Web Dashboard

Access from any device on the network: `http://{LOCAL_IP}:5000`

| Page | URL | Description |
|---|---|---|
| Home | `/` | Live AGV status: mode, sensor, PLC I/O, sequence engine |
| Manual | `/manual` | Web-based manual jogging + pusher control |
| IO Monitor | `/io` | Real-time DI/DO state with profile-derived channel labels |
| Parameters | `/params` | Read-only profile view + runtime tuning toggles and auto speeds |
| Mappings | `/mappings` | RFID→action rule editor (see below) |
| Errors | `/errors` | Timestamped event log |

### Runtime tuning (Parameters page)

Without restarting, operators can toggle:
- **Lidar inner stop** / **Lidar middle slow** — enable/disable protective stop and slow zones
- **RFID enabled** — disable all sequence dispatch while keeping dashboard display live
- **Auto speeds** (HIGH / SLOW / EXTRA_SLOW in m/s) — takes effect on next acceleration cycle

---

## RFID Mappings

Station tags are read by the RFID reader and matched against a rule list. Rules are managed via the `/mappings` web page and saved to `profiles/{AGV_ID}_sequences.json` — the profile's `sequences[]` is never modified.

### Preset rule types

| Type | Trigger | Behaviour |
|---|---|---|
| **End cycle** | Single tag | Stop → pusher retract → mark at_home=true → return to ARMED |
| **Start cycle** | Single tag (requires at_home=true) | Stop → pusher extend → mark at_home=false → resume HIGH |
| **Slow zone** | Paired tags | Start tag switches to SLOW; end tag switches back to HIGH |
| **Timed pause** | Single tag | Stop for N seconds, then auto-resume |
| **Pause until tag** | Paired tags | Stop at start tag; resume when resume tag is scanned |
| **Pulse pusher** | Single tag | Stop → extend or retract pusher for N seconds → resume |

### Apply timing

- **Confirm** saves rules to disk immediately and sets a *reload pending* flag.
- Rules take effect when the AGV next enters **ARMED** mode.
- **Apply now** forces an immediate reload (rejected if a sequence is actively running).

### Audit and rollback

Every save rotates the previous file to `.bak.1` (up to `.bak.5`). The **History** tab shows all saves with timestamps; **Restore backup** replays any slot.

### Export / Import

Use the Export JSON button to download the mapping file. Import it on another AGV to replicate the configuration without manual re-entry.

### First open

When no override file exists, the mapping page automatically imports the profile's factory sequences as editable presets. You must save explicitly before they take effect — the profile file is never touched.

---

## Mode State Machine

```
                 ┌──────────────────────────────────────────┐
                 │              EMERGENCY                    │
                 │  (DI_EMERGENCY active)                   │
                 └──┬───────────────────────────────────────┘
                    │ RESET + switch position
          ┌─────────▼──────────┐
          │       ARMED        │◄─── end_cycle / reset / sensor error
          │  (tape-follow ready│
          └─────────┬──────────┘
              START │ + tape detected
          ┌─────────▼──────────┐
          │      RUNNING       │
          │  (auto tape-follow │
          │   + sequences)     │
          └────────────────────┘

MANUAL ◄──► ARMED/RUNNING  (mode switch live)
```

**DI conventions:**
- `DI_EMERGENCY` — NO contact; `True` = triggered
- `DI_MODE_SWITCH` — `HIGH` = MANUAL by default; set `MODE_SWITCH_INVERT=1` to flip
- `DI_START` / `DI_RESET` — momentary NO, rising-edge detected

---

## Architecture

```
main.py  ─── asyncio.gather ──────────────────────────────────────────────
              │
              ├─ DIReader.run()         Modbus TCP DI poll (50 ms) → di_queue
              ├─ DOWriter.run()         do_queue → Modbus TCP coil writes
              ├─ AOWriter.run()         ao_queue → Modbus TCP register writes
              ├─ CANReader.run()        CAN bus → sensor_queue
              ├─ RFIDReader.run()       TCP socket → rfid_queue
              ├─ SLMPDriver.run()       sync PLC cycle (20 ms, run_in_executor)
              ├─ safety_watchdog()      polls driver health (50 ms)
              ├─ rfid_processor()       rfid_queue → SequenceEngine
              ├─ horn_controller()      manages regular/alarm horn DO outputs
              └─ mode_manager()         central FSM; spawns:
                    ├─ manual_mode()   pendant + web jogging
                    └─ auto_mode()     PID tape-follow + sequence hooks

Flask daemon thread:
              app/app.py               dashboard + API endpoints

Shared state:
              AMRState                 queues + domain objects (GIL-safe)
```

---

## Project Structure

```
agv-kim2a-controller/
├── main.py                     Entry point
├── config.py                   Profile loader — reads profiles/{AGV_ID}.json
├── state.py                    AMRState + typed domain objects
├── modes.py                    auto_mode, manual_mode, mode_manager
├── motion.py                   Drive primitives, kinematics, pusher
├── rfid_processor.py           RFID tag dispatcher
├── horn_controller.py          Regular / alarm horn task
├── safety_watchdog.py          Driver health monitor
│
├── profiles/
│   ├── agv1_kim.json           AGV A profile (with PLC)
│   ├── agv2_kim.json           AGV 2 profile
│   ├── agv_tn.json             AGV B (TN) profile — pusher + lidar
│   ├── agv_tn_sequences.json   RFID mapping override (created by UI)
│   └── agv_tn_sequences.audit.jsonl  Change log (appended by UI)
│
├── core/
│   ├── sequence_engine.py      Declarative RFID/marker sequence runner
│   ├── mapping_store.py        RFID mapping persistence + compile/validate
│   └── pid.py                  PID controller with filtered derivative
│
├── drivers/
│   ├── base.py                 SensorDriver / ActuatorDriver ABCs
│   ├── modbus_di.py            DI reader (Modbus TCP)
│   ├── modbus_do.py            DO writer (Modbus TCP)
│   ├── modbus_ao.py            AO writer (Modbus TCP)
│   ├── can_mgs1600.py          CANopen sensor reader (MGS1600)
│   ├── rfid_tcp.py             RFID TCP reader
│   └── slmp_plc.py             Mitsubishi PLC SLMP driver
│
├── app/
│   ├── app.py                  Flask server + all API endpoints
│   └── templates/
│       ├── index.html          Live dashboard
│       ├── manual.html         Web jogging UI
│       ├── io_monitor.html     DI/DO monitor
│       ├── params.html         Profile view + runtime tuning
│       ├── mappings.html       RFID mapping editor
│       └── errors.html         Event log
│
├── logs/                       Rotating log files (agv_YYYYMMDD_HHMMSS.log)
├── requirements.txt
└── project_overview.json       Quick-start reference for new LLM instances
```

---

## Logging

Logs are written to `logs/` (rotating, 10 MB × 5 files) and to the console.

| Level | Used for |
|---|---|
| `DEBUG` | PID loop values, raw sensor frames (console-suppressed by default) |
| `INFO` | Connections, mode transitions, sequence events |
| `WARNING` | Tape loss, CAN timeout, driver reconnects |
| `ERROR` | Comms failures, unhandled exceptions |
| `CRITICAL` | Emergency stop |
