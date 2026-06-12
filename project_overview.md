# AGV KIM2A Controller — Project Overview

> **Audience:** an LLM coding agent (Claude Opus / GPT-class) with no prior context on this project, its hardware, or its code. Read this before touching any file. It explains *what the system is*, *how it is built*, and *which protocols talk to which hardware*.
> **Scope note:** the **trolley** and its **PLC / SLMP link** are an **optional add-on**.
> A *baseline* KIM2A-family AGV is a tape-following differential-drive robot with **no PLC at all**. Everything PLC/SLMP/trolley-related is isolated in the [Add-on: Trolley + PLC](#add-on-trolley--plc-via-slmp) section so you can mentally delete it for a non-trolley AGV. Most AGVs in this fleet follow the core architecture *without* the add-on.

---

## 1. What this is

A controller for an **Autonomous Guided Vehicle (AGV)** — a differential-drive
(two independently-driven wheels) robot that follows a **magnetic guide tape** laid on the
floor. It is an industrial line vehicle: it drives a fixed tape loop, slows for corners,
stops at stations, and (with the add-on) hands off to a PLC to load/unload a trolley.

- **Language / runtime:** Python 3, `asyncio`. One event loop runs all control logic
  concurrently; a Flask web dashboard runs in a separate daemon thread.
- **Entry point:** [main.py](main.py)
- **Run:** `AGV_ID=agv1_kim python3 main.py`
- **Deployment target:** an Ubuntu PC mounted on the AGV, run as a **systemd service**
  named `agv-controller.service`. (Several code paths restart this service — see
  [§9 Recovery](#9-emergency--recovery).) The repo is developed on Windows but **cannot be
  run there** — the hardware drivers (Modbus devices, CANable USB adapter, PLC) only exist on the vehicle.
- **Config selection:** the `AGV_ID` environment variable picks a profile under
  [profiles/](profiles/) (default `agv1_kim`). All per-vehicle tuning lives in that JSON;
  there is no code change between vehicles.

### Physical I/O at a glance

| Subsystem | Hardware | Bus / protocol | Library |
|---|---|---|---|
| Digital inputs (buttons, selectors, prox) | Remote DIO module | **Modbus TCP** (discrete inputs) | `pymodbus` (async) |
| Digital outputs (motor dir/brake coils) | Remote DIO module | **Modbus TCP** (coils) | `pymodbus` (async) |
| Analog outputs (motor speed DAC, 0–10 V) | Remote AO module | **Modbus TCP** (holding regs) | `pymodbus` (async) |
| Magnetic guide sensor | Roboteq **MGS1600** | **CAN** (custom frame) over **slcan** / CANable2 USB | `python-can` |
| RFID floor-tag reader | TCP RFID reader | **raw TCP socket** (hex stream) | `socket` |
| Wheel-speed encoder *(calibration only)* | CANopen friction-wheel encoder | **CANopen** (TPDO/SDO) over slcan / CANable2 | `python-can` |
| **PLC** *(add-on)* | Mitsubishi **FX5U** | **SLMP / MC Protocol** (Type3E binary) | `pymcprotocol` |
| Web dashboard | operator phone/laptop over WiFi | HTTP | `Flask` |

Dependencies are pinned in [requirements.txt](requirements.txt).

### Network topology

The Ubuntu controller has **multiple NICs / VLANs** — this is a deliberately *segmented*
industrial network, not a flat one. Each device group lives on its own subnet (values from
the `agv1_kim` profile):

| Subnet | Devices |
|---|---|
| `192.168.1.x` | DIO module (`.31`), AO module (`.30`) — Modbus TCP, port 502 |
| `192.168.2.x` | Controller (`.100`) / HMI WiFi — Flask dashboard on `:5000` |
| `192.168.4.x` | PLC (`.10:1280`) — *add-on only* |
| `192.168.5.x` | RFID reader (`.10:2022`) |

---

## 2. Process & concurrency model

```
asyncio event loop (single thread)
├─ AOWriter.run            — consumes ao_setpoints → Modbus holding regs (DAC)   [always]
├─ DOWriter.run            — consumes do_setpoints → Modbus coils                 [if DIO_ENABLED]
├─ DIReader.run            — Modbus discrete-input poll → latest_di + di_queue    [if DIO_ENABLED, watched]
├─ safety_watchdog         — polls sensor-driver health every 50 ms              [always]
├─ rfid_processor          — drains rfid_queue → SequenceEngine.on_rfid_tag()    [always]
├─ modes.mode_manager      — the central FSM; spawns manual/auto/calibrate tasks [always]
└─ DriverManager-owned (toggleable live from the HMI):
   ├─ CANReader.run        — MGS1600 magnetic sensor → latest_sensor + queue     [if CAN_ENABLED, watched]
   ├─ RFIDReader.run       — TCP RFID → rfid_queue                               [if NAV_ENABLED, watched]
   ├─ (SEQ has no task — it is a pure gate flag read by the SequenceEngine)
   └─ SLMPDriver.run       — PLC read/write cycle every 20 ms                    [if SLMP_ENABLED]  ← add-on

Flask daemon thread (separate): serves the dashboard, mutates a few state fields.
```

All tasks share **one** [`AMRState`](state.py) object passed by reference. There is no
message passing between tasks except through the queues/setpoint-tables inside that object.
The Flask thread touches the same object — this is GIL-safe for reads and for the
single-attribute writes it performs (`plc_sequence_request`, `web_manual_command`,
`calibration_request`, `trolley.write_bits[...]`).

**Golden rule:** never block the event loop. Every synchronous hardware call (`pymodbus`
sync paths, `pymcprotocol`, CAN bus construction) is dispatched via
`loop.run_in_executor(...)`.

---

## 3. Shared state — [state.py](state.py)

`AMRState` is the single source of truth. It is split into typed sub-domains, but every
field is also exposed as a **flat property shim** on `AMRState` so call sites can read/write
`state.current_mode`, `state.speed_mode`, etc. directly.

### Cross-task channels

- **Inputs use `asyncio.Queue`** (producer drivers → consumer tasks), each drained
  *keep-latest* every cycle so stale frames never accumulate:
  - `di_queue` — DI bit arrays (DIReader → manual_mode)
  - `sensor_queue` — MGS1600 CAN frames (CANReader → auto_mode, and mode_manager peeks it
    on START to confirm tape)
  - `rfid_queue` — 4-char hex tag strings (RFIDReader → rfid_processor)
- **Outputs use latest-wins setpoint tables**, *not* queues — this is a key change from
  older designs:
  - `ao_setpoints: {channel: voltage}` + `ao_dirty: asyncio.Event`
  - `do_setpoints: {channel: bool}` + `do_dirty: asyncio.Event`
  - Producers call `state.set_ao(ch, v)` / `state.set_do(ch, bool)`. The writer tasks wake
    on the dirty Event and assert the **latest** value per channel. This bounds memory and,
    crucially, **prevents replay of a stale command backlog after a comms reconnect** (a
    FIFO queue would flush the backlog). A group of `set_*` calls with no `await` between
    them is applied atomically before a writer can wake.

### State domains

| Domain | Owner(s) | Key fields |
|---|---|---|
| `SystemState` | mode_manager, safety_watchdog | `current_mode` (`None`/`manual`/`armed`/`running`/`calibrate`/`emergency`), `emergency_active`, `system_error` |
| `KinematicState` | sequence_engine, mode_manager | `speed_mode` (`HIGH`/`SLOW`/`APPROACH`), `sequence_stop`, `pending_sequence`, `nav_in_corner` (gates feedforward), `web_manual_command`+`web_manual_expire`, `calibration_request`+`calibration_wheel` |
| `PerceptionState` | hardware drivers, auto_mode | `latest_di`, `latest_do`, `latest_sensor`, `can_last_rx`; motion telemetry `left_rpm`/`right_rpm`/`pid_output`/`target_speed`; encoder telemetry `encoder_*` |
| `PLCState` *(add-on)* | SLMPDriver | `plc_inputs` (M0–M4), `plc_sequence_request` (int → one-hot, with `plc_sequence_pulse_expire`), `plc_sequence_complete` |
| `TrolleyState` *(add-on)* | SLMPDriver | `y_bits`, `x_bits`, `top_m_bits`, `bot_m_bits`, `write_bits` |

Also top-level: `latest_rfid_tag`, and `calibration_status` (live dict read by the
calibration dashboard).

---

## 4. Configuration — [config.py](config.py) + profile JSON

`config.py` loads `profiles/{AGV_ID}.json` at import (falling back to a legacy
`parameters.json`), flattens it into module-level constants, and runs **`_validate()`** — a
fail-fast range check that raises `ConfigError` at import if a profile is unsafe
(non-positive wheel diameter / gear ratio / DT / clamps, speeds outside `[0, 5] m/s`,
ports outside `1..65535`, etc.). The controller refuses to start on a bad profile rather
than dividing by zero or running away mid-motion.

Profile sections (see [profiles/agv1_kim.json](profiles/agv1_kim.json)):

- **`networking`** — the per-subnet IPs/ports in §1.
- **`io_mapping`** — `DI_BASE/DO_BASE/AO_BASE`, `NUM_DI/DO/AO`, and the *logical* DI bit
  indices: `DI_EMERGENCY`, `DI_MODE_SWITCH`, `DI_START`, `DI_RESET`, `DI_FWD/REV/LEFT/RIGHT`,
  `DI_PROX` (magnetic proximity station marker, `-1` = none). `DI_FLIPPED` inverts all DI
  bits at read time (for modules where *no input = ON*).
- **`motor_channels`** — per-side `{do_fwd, do_rev, do_brake, ao_speed}` channel indices.
  **Motion never hardcodes DO 0–5; it always reads this map.**
- **`hardware`** — `V_RANGE` (10 V), `DAC_RES` (4095), and the **per-wheel motor speed
  calibration** `rpm = RPM_PER_VOLT[side] * V + RPM_VOLT_OFFSET[side]`. Left/right differ
  ~6 %; side-aware inversion corrects both the speed scale and L/R drift.
- **`kinematics`** — `WHEEL_DIAMETER`, `GEAR_RATIO` (→ `WHEEL_CIRCUMFERENCE`).
- **`speeds`** — manual/auto HIGH/SLOW + auto APPROACH targets (m/s), plus `ACCEL_RATE`,
  `DECEL_RATE`, `APPROACH_DECEL_RATE`.
- **`pid_tuning`** — three gain sets (HIGH / SLOW / APPROACH), shared integral/timing
  constants, output clamp, speed-reduction coefficients. See [§6 PID](#6-pid-controller).
- **`can_sensor`** — `SENSOR_COB_ID`, flag bitmasks (`FLAG_TAPE_DETECT/LEFT_MARKER/RIGHT_MARKER/SENSOR_FAIL`),
  `CAN_TIMEOUT`, `CAN_NODE_ID`, and sensor sanity limits `SENSOR_MAX_MM` / `SENSOR_MAX_STEP_MM`.
- **`rfid`** — `RFID_INIT_CMD_HEX` (sent on connect), `SEQUENCE_STOP_DELAY`.
- **`feedforward`** — curvature feedforward geometry (see [§6](#6-pid-controller)).
- **`encoder`** + **`calibration`** — *calibration-only* (see [§8](#8-wheel-speed-calibration-mode)).
- **`sequences`** — declarative sequence list (see [§7](#7-sequence-engine)).
- **`features`** — the five live-toggleable flags (see below).
- **`watchdog`** / **`timing`** — driver-health timeouts and DI poll interval.
- *(add-on)* **`slmp_plc`** / **`slmp_manual`** — PLC register documentation (descriptions
  and R / R-W permissions for the dashboard).

### Feature flags & live toggling

Five booleans gate optional subsystems, with a **dependency hierarchy**:

```
DIO_ENABLED   — DI + DO Modbus module (fixed at boot)
CAN_ENABLED   — MGS1600 magnetic sensor (independent; released during calibration)
NAV_ENABLED   — RFID reader + corner speed-zone sequences  ┐
SEQ_ENABLED   — stop / wait / PLC-handshake sequences       │ NAV → SEQ → SLMP
SLMP_ENABLED  — PLC link (add-on)                           ┘
```

`config.py` enforces the hierarchy at load (a child can't outlive its parent). At runtime
the **`DriverManager`** (in [main.py](main.py)) starts/stops these subsystems *live* from
the HMI: enabling a child requires its parent on; disabling a parent cascades to children.
The Flask thread calls `set_enabled()`, which marshals the coroutine onto the event loop
via `run_coroutine_threadsafe`. Toggles are **not persisted** — on restart the controller
boots from the profile JSON. `SEQ` has no driver task of its own; it is a pure gate flag
the SequenceEngine reads. CAN is managed (not a fixed task) specifically so calibration can
release the shared CANable adapter for the encoder and restore it afterward.

---

## 5. Mode state machine — [modes.py](modes.py)

`mode_manager` is the central FSM and the **only** task that owns mode transitions and
spawns/cancels the mode subtasks (`manual_mode`, `auto_mode`, `calibration.calibrate_mode`).
It reads the **physical control panel** via `latest_di` (these buttons are wired to the
*Modbus DIO module* — they are the AGV's own panel and are entirely independent of the
PLC's M0–M4 signals).

DI conventions:
- `DI_MODE_SWITCH`: HIGH = MANUAL selector, LOW = AUTO selector
- `DI_EMERGENCY`: NO contact — `True` = triggered (not safe)
- `DI_START`, `DI_RESET`: momentary NO — act on the rising edge

States: `None → manual | armed | running | calibrate | emergency`.

| Transition | Trigger |
|---|---|
| `None → armed` | startup always enters ARMED first (lets DO/AO Modbus writers connect); flips to MANUAL next loop if the selector is there |
| `* → manual` | `DI_MODE_SWITCH` HIGH (live switch) |
| `manual → armed` | `DI_MODE_SWITCH` LOW |
| `armed → running` | `DI_START` rising **and** tape detected (peeks `sensor_queue`; START is refused off-tape) |
| `running → armed` | `DI_RESET` rising |
| `armed → calibrate` | `state.calibration_request` set from HMI (AUTO selector, safe) |
| `calibrate → armed` | `DI_RESET` rising **or** request cleared |
| `* → emergency` | `DI_EMERGENCY` triggered |
| `emergency → manual/armed` | `DI_RESET` rising (target depends on selector) |

Cross-cutting guards (checked every loop):
- **Emergency** cancels the active task and asserts **idle** (not brake) — brakes are
  *released* so the AGV can be pushed clear by hand. Holding `DI_RESET` for 3 s while in
  emergency triggers a **controller service restart** (see [§9](#9-emergency--recovery)).
- **`system_error`** (set by the watchdog on driver comms loss) cancels the active task and
  brakes until the watchdog clears it.
- Every transition calls `_reset_sequence_state()` → resets `speed_mode`, clears
  `sequence_stop`/`nav_in_corner`/`pending_sequence`, and `engine.cancel_armed()`.

### `auto_mode` — forward tape-following (the only auto motion)

> Note: an older *reverse* auto-follow path has been **removed**. Auto is forward-only now.

A PID loop (see §6) running at ~`DT`. Each cycle it:
1. Handles emergency / `sequence_stop` (brake & hold) up front.
2. Selects the target speed and PID gain set from `speed_mode` (`HIGH`/`SLOW`/`APPROACH`),
   switching gains only on change.
3. Drains `sensor_queue` keep-latest. On **tape lost** or **CAN timeout**
   (`now - can_last_rx > CAN_TIMEOUT`) → brake, reset PID, wait to reacquire.
4. Fires **marker hooks** into the SequenceEngine: CAN `left_marker`/`right_marker`, and a
   rising-edge **`prox`** marker from `DI_PROX` (only when SEQ+SLMP enabled).
5. **Sensor sanity**: rejects `|left_mm| > SENSOR_MAX_MM` frames and slew-limits per-cycle
   jumps to `SENSOR_MAX_STEP_MM` to suppress glitch frames.
6. Ramps `current_target_speed` toward target (`ACCEL_RATE`/`DECEL_RATE` × `DT`), computes
   PID, writes per-wheel voltages via `set_ao`, and records telemetry + a plot buffer.

### `manual_mode` — pendant + web remote

Drains `di_queue` and maps button combinations (`fwd+left`, `rvs+right`, single buttons,
idle) to motion primitives. The **web remote** (`state.web_manual_command`, set by
`POST /api/manual/command`) takes **priority** over physical buttons and auto-expires after
a 400 ms watchdog so a dropped connection stops the AGV. Emergency is owned entirely by
mode_manager; manual_mode does not check it.

---

## 6. PID controller — [core/pid.py](core/pid.py) + [motion.py](motion.py)

`PIDController` steers by driving the **lateral offset** (MGS1600 `left_mm`) to zero. It is
extracted from the mode loop so it can be reused.

- **Process variable:** `pv = left_mm` (mm off tape centre). `error = error_sign * pv`
  (`error_sign = -1.0` for forward).
- **P**: `kp * e`.
- **I**: accumulates only inside a **deadband** (`|e| < TI_DEADBAND`), anti-windup clamped
  to `±TI_MAX`; term is `kp * (1/TI) * integral`.
- **D**: low-pass filtered derivative, coefficient `alpha = dt / (td/n + dt)`, computed from
  the **measured** loop period (clamped to a sane band), not nominal `DT`.
- **Output clamp** `±OUTPUT_CLAMP_RPM` applies to the PID steering term only.
- **Feedforward**: an optional curvature term is *added after* the clamp (a known-good
  open-loop command). In a sustained corner a pure-feedback loop settles with a standing
  cross-track error; the geometric feedforward
  `sign * 0.5 * base_rpm * (TRACK_WIDTH / CURVE_RADIUS)` removes that lag. It is low-pass
  filtered (`FF_ALPHA`) and only applied while `nav_in_corner` is set (a NAV sequence holds
  `SLOW` in a corner zone). Configured under the profile `feedforward` section.
- **Speed reduction**: a filtered, capped (`SR_CAP * base_rpm`) term that slows *both*
  wheels when error/derivative is large (slows into sharp deviations).
- **Output mixing:** `left_rpm = base - sr + out_total`, `right_rpm = base - sr - out_total`.
- `update_gains()` switches gain sets live **without** resetting the integrator (smooth
  HIGH↔SLOW↔APPROACH transitions); `reset()` zeros all integral/filter state.

`motion.py` converts between speed/rpm/voltage and emits the actual `set_do`/`set_ao`
commands:
- `mps_to_rpm(v)` = `(v·60 / WHEEL_CIRCUMFERENCE) · GEAR_RATIO`.
- `rpm_to_voltage(rpm, side)` = side-aware inverse of the motor cal, clamped to `[0, 5] V`;
  `rpm ≤ 0 → 0 V` (a true stop, no creep from the affine offset).
- `_drive(...)` sets both wheels' direction coils + speed AO from `MOTOR_CHANNELS`; helpers
  `set_forward/reverse/left/right`, the diagonal combos, `set_brake` (brake coils on,
  0 V), and `idle` (all coils off, 0 V — brakes released).

---

## 7. Sequence engine — [core/sequence_engine.py](core/sequence_engine.py)

A **declarative, JSON-driven** runner: adding or changing route behaviour means editing the
profile `sequences` list — **zero Python changes**. Adding a new *action type* means adding
one method and registering it (`register_action(name, handler)`).

Each sequence has a `trigger`, an ordered `actions` list, a `cooldown_s`, and an optional
`requires_mode`. Triggers:

- **`rfid`** — fires immediately when a matching RFID tag is read.
- **`marker`** — fires when a matching tape marker (`left`/`right`) is detected by auto_mode.
- **`rfid_then_marker`** — RFID **arms** an approach (slows to a configurable `approach_speed`,
  records `pending_sequence`); the later marker fires execution and skips the already-done
  approach steps. The marker may be `left`/`right` (CAN) or **`prox`** (the `DI_PROX`
  proximity sensor — used for precise station stops independent of the tape frame).

Built-in actions: `set_speed`, `wait_marker`, `stop_agv`, `resume`, `sequence_stop` (timed
stop+auto-resume), `wait_seconds`, and *(add-on)* `plc_request` / `wait_plc_complete`.

**Category gating:** each sequence is classified `navigation` (pure `set_speed` corner
zones, gated by `NAV_ENABLED`) or `sequence` (anything that stops/waits/talks to the PLC,
gated by `SEQ_ENABLED`) — explicit `"category"` in JSON wins, else inferred.

Concurrency & safety: at most **one** sequence runs at a time (`_active_sequence` guard);
preconditions are `requires_mode` + cooldown + no-active-sequence; `cancel_active()` and
`cancel_armed()` are called by mode_manager on transitions to flush stale state. A PLC
handshake that never completes raises `SequencePLCFault` — the AGV **holds stopped (fault)**
and requires an operator RESET; it must not silently resume (`SequenceTimeout`, by contrast,
auto-resumes).

---

## 8. Wheel-speed calibration mode — [calibration.py](calibration.py)

A maintenance/commissioning tool to measure **commanded-vs-actual** wheel speed and derive
the per-wheel motor calibration (`RPM_PER_VOLT` / `RPM_VOLT_OFFSET`) that lives in the
profile `hardware` section.

- Launched by mode_manager (`armed → calibrate`) when the HMI sets `calibration_request`.
- Drives **both wheels straight** through an open-loop stepped ramp (`CAL_V_START →
  CAL_V_MAX`, stepping `CAL_V_STEP` every `CAL_DWELL_S`) — no PID, no tape needed.
- A **CANopen friction-wheel encoder** ([drivers/can_encoder.py](drivers/can_encoder.py))
  measures actual ground speed (`v = ω · ENCODER_RADIUS_M`). The encoder is a
  **calibration-only tool**, temporarily attached — it is *not* a permanent fixture.
- It **shares the single CANable USB adapter** with the MGS1600. Only one driver may own the
  port, so calibration stops the CAN (MGS1600) driver via the DriverManager, runs the
  encoder, and **restores** the MGS1600 driver on exit (the `finally`/`_cleanup` is
  `asyncio.shield`-ed so a cancel still idles motors and restores CAN).
- Logs to CSV + PNG under `analysis_plot/`. Live status is exposed at
  `/api/calibration/*`.

The encoder driver speaks CANopen (NMT start, optional SDO geometry read of objects
`0x6001`/`0x6002`, multi-turn TPDO count with rollover at `ENCODER_TOTAL_RANGE`) at the
encoder's own bitrate (125 kbit/s), distinct from the MGS1600's 500 kbit/s.

---

## 9. Safety, watchdog & recovery

### Safety watchdog — [safety_watchdog.py](safety_watchdog.py)
Polls a shared list of `(SensorDriver, timeout_s)` pairs every 50 ms (DI, CAN, RFID — the
*input* drivers; output drivers fail visibly as "motion not responding" and aren't watched).
If any driver's last successful read is older than its timeout → sets `system_error` and
**immediately zeros both speed AOs** without waiting for mode_manager. Clears `system_error`
when all drivers recover. The `watched` list is mutated live by the DriverManager as
toggleable drivers come and go.

Driver health comes from the [drivers/base.py](drivers/base.py) `SensorDriver` ABC:
`_record_rx()` on every good read, `get_health() → {ok, last_rx, detail}`.

### Emergency & recovery
- **Emergency** (`DI_EMERGENCY`) → idle (brakes released), cancel active task, set
  `emergency_active`. Recovery requires a `DI_RESET` rising edge.
- **Hold `DI_RESET` for 3 s during emergency** → fire-and-forget `systemctl restart
  agv-controller.service` (recovers a hung controller without a terminal). The same restart
  is exposed to operators at `POST /api/restart`. Both require a passwordless sudoers rule
  for exactly that command.

### Shutdown — [main.py](main.py)
`SIGTERM`/`SIGINT` cancels all tasks; on `CancelledError` the controller opens fresh Modbus
clients and **directly zeros all AO registers and DO coils**, bypassing the setpoint tables,
so the AGV is left safe.

### Logging — [logger.py](logger.py)
`setup_logging()` (called once from main) installs a **non-blocking** `QueueHandler` →
`QueueListener` so file/console I/O never jitters the control loop. Outputs: rotating file
(INFO+, 10 MB × 5 in `logs/`), console (INFO+, `DEBUG` for PID tuning), and an in-memory
ring buffer (WARNING+, 500 entries) surfaced on the `/errors` dashboard page. Third-party
noise (werkzeug, pymodbus, can) is silenced. Level conventions: DEBUG = per-cycle PID/sensor;
INFO = connections/mode transitions/sequence events; WARNING = recoverable (tape loss, CAN
timeout, reconnect); ERROR = comms failures; CRITICAL = emergency stop.

---

## 10. Web dashboard — [app/app.py](app/app.py) + [app/templates/](app/templates/)

Flask in a daemon thread, served at `http://<LOCAL_IP>:5000`. It shares the `AMRState`
object directly and also holds references to the `SequenceEngine` and the `DriverManager`.
The HTML pages (top nav: **HOME · MANUAL · IO · TROLLEY · PARAMS · ERRORS**) poll JSON APIs
and reveal which features matter operationally:

| Page | Route | Purpose |
|---|---|---|
| **Home** | `/` → `/api/state` | Live mode, emergency/error, speed mode, sensor (offset, markers, per-wheel RPM/velocity, PID output), sequence-engine status, and *(add-on)* PLC read/write bits |
| **Manual** | `/manual` → `/api/manual/command` | On-screen jog remote (forward/reverse/turn/diagonals); only works in `manual` mode; 400 ms watchdog |
| **IO** | `/io` → `/api/io` | Live DI/DO bit monitor with human labels derived from the profile mapping |
| **Params** | `/params` → `/api/params`, `/api/params/features`, `/api/calibration/*` | Read-only parameter dump; **live feature-flag toggles** (CAN/NAV/SEQ/SLMP, blocked while running/emergency); start/stop **wheel calibration** |
| **Errors** | `/errors` → `/api/errors`, `/api/restart` | The WARNING+ ring buffer; operator **controller restart** button |
| **Trolley** *(add-on)* | `/trolley` → `/api/trolley/*` | Manual trolley conveyor/pusher control + PLC X/Y/M bit view |
| — | `/api/sequence` | *(add-on)* Manually inject/clear a PLC sequence request (gated on PLC `MASTER_ON` + `MODE_MAN`) |
| — | `/api/wifi` | WiFi SSID/signal via `nmcli` |

---

## Add-on: Trolley + PLC (via SLMP)

> **Everything below is the optional add-on.** A baseline AGV has **no PLC**; `SLMP_ENABLED`
> is off, the trolley page and PLC-handshake sequences are inert, and the
> `PLCState`/`TrolleyState` domains simply stay at their defaults.

The add-on integrates the AGV with a **Mitsubishi FX5U PLC** that controls a loading
**trolley** (top/bottom conveyors + pusher) at stations. The AGV does not drive the trolley
motors itself — it **requests** an operation and waits for the PLC to report completion.

### SLMP driver — [drivers/slmp_plc.py](drivers/slmp_plc.py)
A single sync read/write cycle runs every 20 ms in a thread-pool worker
(`pymcprotocol.Type3E`, binary). Each cycle:

- **Writes AGV status** `M2000–M2005`: `READY`, `EMERGENCY`, `REQ TROLLEY EMERGENCY`,
  `MANUAL`, `AUTO`, `RUNNING` (derived from `current_mode`/`emergency_active`).
- **Writes the one-hot sequence request** `M2010–M2018` (9 bits) from
  `plc_sequence_request`, auto-cleared after `plc_sequence_pulse_expire`.
- **Reads PLC inputs** `M0–M4` → `plc_inputs`: `PB_START_AUTO`, `MODE_MAN`, `MODE_AUTO`,
  `EMERGENCY`, `MASTER_ON`. **These are an independent control panel on the PLC/trolley side
  — they are *not* the AGV's own DIO buttons** and only gate manual sequence injection via
  `/api/sequence`; the AGV's motion FSM never reads them.
- **Reads sequence-complete flags** `M2040–M2048` → `plc_sequence_complete`.
- **Trolley control:** writes the operator's requested manual bits (`M2104–M2109` top conv,
  `M2124–M2129` bottom conv, `M2212–M2213` pusher) from `trolley.write_bits`, and reads back
  the trolley `M`, `Y` (outputs), and `X` (inputs/proximity) bit banks for the dashboard.

### PLC-handshake sequences
The `rfid_then_marker` sequences in the profile (`trolley_load_00..05`) implement station
loading: arm on RFID → approach slowly → stop on the marker (`prox` or `left`) → pulse a
PLC `plc_request` → `wait_plc_complete` (Phase A: wait for the complete flag to drop = PLC
acknowledged; Phase B: wait for it to rise = done, bounded by `done_timeout`) → resume. A
done-timeout raises `SequencePLCFault` and holds the AGV until an operator RESET.

---

## Repository map & conventions

### Layout
```
main.py                 entry point, DriverManager, task wiring, shutdown
config.py               profile loader + fail-fast validation
state.py                AMRState + typed domains + property shims
modes.py                mode_manager FSM, auto_mode, manual_mode
motion.py               kinematics + drive primitives (reads MOTOR_CHANNELS)
calibration.py          open-loop wheel-speed calibration mode
safety_watchdog.py      sensor-driver health monitor
rfid_processor.py       rfid_queue → SequenceEngine dispatcher
logger.py               non-blocking logging + /errors ring buffer
core/pid.py             PIDController
core/sequence_engine.py declarative JSON sequence runner
drivers/base.py         SensorDriver / ActuatorDriver ABCs
drivers/modbus_di.py    DIReader   (Modbus discrete inputs)
drivers/modbus_do.py    DOWriter   (Modbus coils)
drivers/modbus_ao.py    AOWriter   (Modbus holding regs / DAC)
drivers/can_mgs1600.py  CANReader  (MGS1600 magnetic guide sensor, slcan)
drivers/rfid_tcp.py     RFIDReader (raw TCP hex stream)
drivers/slmp_plc.py     SLMPDriver (FX5U PLC, add-on)
drivers/can_encoder.py  EncoderReader (CANopen, calibration only)
profiles/*.json         per-AGV config (AGV_ID selects)
app/app.py              Flask dashboard
app/templates/*.html    dashboard pages
debugging/*.py          standalone hardware test/monitor scripts (see below)
analysis_plot/          calibration & PID run CSV/PNG output
```

### Legacy / compatibility shims (don't write new code against these)
- `io_hardware.py` — re-exports all drivers; import from `drivers/*` instead.
- `slmp_handler.py` — a standalone duplicate of the SLMP logic; **not** used by `main.py`.
- Each driver file also exposes a module-level coroutine (`di_reader`, `can_reader`, …)
  wrapping a private `_instance`, for backwards compatibility.
- `config.py` still falls back to a root `parameters.json` if no profile matches `AGV_ID`.

### Debugging tools — [debugging/](debugging/)
Standalone scripts (run directly, mostly *not* wired through `config.py`) for bring-up and
verification: `check_connections.py` (ping all devices + CANable presence),
`read_dio.py`/`write_do.py`/`analog_output.py` (Modbus I/O), `can_reader.py`/`debug.py`
(CAN + multi-subsystem monitor), `rfid_reader.py`, `motor_test.py` (⚠ physically moves the
AGV), `slmp_tester10.py` (PLC connectivity), `read_can_encoder.py` /
`read_encoder_info.py` / `verify_wheel_speed.py` (encoder), and `plotter.py` (the
`RunRecorder` imported by `auto_mode` to dump PID run plots).

### Key design rules
1. **Never block the event loop** — all sync hardware calls go through `run_in_executor`.
2. **Outputs are latest-wins setpoint tables**, not FIFO queues — no stale replay after a
   reconnect. Inputs are keep-latest queues.
3. **All motion goes through `motion.py` `set_do`/`set_ao`** — never write DO/AO directly
   except in `shutdown()`.
4. **`mode_manager` owns all mode transitions**; `auto_mode`/`manual_mode`/calibration are
   passive subtasks it spawns and cancels.
5. **`mode_manager` and `safety_watchdog` are the only tasks that halt motion unilaterally.**
6. **One sequence at a time**; the profile JSON is the only place to add/modify sequences.
7. **Channel/IO mapping is never hardcoded** — always read from `config` / `MOTOR_CHANNELS`.
8. **`DI_FLIPPED` inverts at read time** in DIReader — all downstream code sees logical values.
9. **The trolley + PLC/SLMP layer is an add-on** — keep it behind `SLMP_ENABLED` and the
   `PLCState`/`TrolleyState` domains so a non-trolley AGV needs no PLC.
```
