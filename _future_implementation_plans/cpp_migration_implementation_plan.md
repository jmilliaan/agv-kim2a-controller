# C++ Migration Implementation Plan for `agv-kim2a-controller` (`tn-feature`)

## Goal

Migrate the timing-sensitive, hardware-adjacent parts of the current Python AGV stack to C++ while preserving the existing Python strengths:

- Python remains the supervisor/orchestrator layer.
- C++ becomes the low-level `vehicle_core` runtime.
- The migration should be incremental, with clear stopping points after each phase.

This document is intended for a coding agent with access to the current codebase.

## Current Codebase Summary

The current Python implementation already has a strong separation between:

- hardware drivers
- motion/control logic
- mode/state management
- declarative sequence logic
- web/API supervision

Relevant current files:

- `repo/main.py`
- `repo/modes.py`
- `repo/motion.py`
- `repo/core/pid.py`
- `repo/state.py`
- `repo/core/sequence_engine.py`
- `repo/drivers/can_mgs1600.py`
- `repo/drivers/modbus_di.py`
- `repo/drivers/modbus_do.py`
- `repo/drivers/modbus_ao.py`
- `repo/drivers/rfid_tcp.py`
- `repo/drivers/slmp_plc.py`
- `repo/app/app.py`
- `repo/profiles/agv_tn.json`
- `repo/config.py`

## Migration Decision

Do **not** port the whole application to C++.

Port only the parts where C++ provides meaningful value:

- tighter timing control
- lower jitter
- stronger fault isolation in the motion path
- better suitability for future higher-rate control or bus load

Keep the high-level behavior in Python where the code is already flexible and productive.

## Architecture Target

Target runtime after migration:

- `agv_supervisor` in Python
  - mode orchestration
  - sequence engine
  - RFID-triggered high-level workflow
  - dashboard/API
  - configuration loading
  - non-time-critical diagnostics

- `vehicle_core` in C++
  - CAN sensor ingest
  - Modbus DI polling
  - Modbus DO writes
  - Modbus AO writes
  - low-level motion command execution
  - tape-follow control loop
  - low-level motion watchdog / command timeout
  - pusher relay sequencing if desired

Communication between Python and C++:

- localhost TCP using JSON messages

This choice is preferred over in-process bindings because:

- it keeps fault domains separate
- it is easier to restart one side independently
- it maps well to `systemd`
- it is easier to inspect and debug in the field

## What Should Move to C++

### 1. `drivers/can_mgs1600.py`

Move to C++: **Yes**

Reasoning:

- This is a hardware-facing continuous reader.
- It feeds the tape-follow loop directly.
- It is part of the critical sensor-to-control path.
- A future increase above the current loop rate or heavier bus load will stress Python first here.

Justification:

- CAN ingest latency and timestamp consistency matter more than Python convenience here.
- It belongs in the same runtime as the low-level controller.

Required functionality to preserve:

- find/open CAN interface
- send startup/init message to sensor
- decode frames into `left_mm`, `right_mm`, `tape_detected`, `left_marker`, `right_marker`, `sensor_failure`
- maintain last-received timestamp
- expose latest frame to the control loop and to Python

Recommended C++ implementation details:

- Linux `SocketCAN` directly if targeting native CAN on Ubuntu
- if the deployment must keep CANable/slcan, support `slcan` device configuration explicitly
- timestamp each received frame in C++

Libraries:

- Linux SocketCAN headers / kernel CAN API
- optional: `libsocketcan` if useful in deployment
- `spdlog`
- `nlohmann/json`

Parameters needed from config/profile:

- `SENSOR_COB_ID`
- `FLAG_TAPE_DETECT`
- `FLAG_LEFT_MARKER`
- `FLAG_RIGHT_MARKER`
- `FLAG_SENSOR_FAIL`
- `CAN_TIMEOUT`
- `CAN_NODE_ID`
- CAN interface name or serial/slcan device path

### 2. `drivers/modbus_di.py`

Move to C++: **Yes**

Reasoning:

- DI polling is part of control state input.
- Emergency-adjacent and mode-adjacent digital inputs should be captured predictably.
- This polling already runs every 50 ms in Python, which is workable, but it sits close to the control edge.

Justification:

- Keeping DI in `vehicle_core` reduces end-to-end latency between input changes and motion response.
- It makes local degraded-mode behavior easier when Python is unavailable.

Required functionality to preserve:

- poll discrete inputs
- apply `DI_FLIPPED`
- publish latest DI snapshot
- retain last successful receive timestamp

Libraries:

- `libmodbus`
- `spdlog`
- `nlohmann/json`

Parameters needed:

- `DIO_IP`
- `MODBUS_PORT`
- `DEVICE_ID`
- `DI_BASE`
- `NUM_DI`
- `DI_FLIPPED`
- poll interval

### 3. `drivers/modbus_do.py`

Move to C++: **Yes**

Reasoning:

- DO commands are used for wheel direction, brake, and pusher relays.
- These outputs should be owned by the same runtime that owns motion and actuator sequencing.

Justification:

- Reduces cross-process command latency.
- Prevents Python task scheduling delays from affecting actuator command timing.
- Important for relay sequencing and future local fail-safe behaviors.

Required functionality to preserve:

- write individual coils
- track current commanded DO state
- support direction/brake commands and pusher relay actions

Libraries:

- `libmodbus`
- `spdlog`
- `nlohmann/json`

Parameters needed:

- `DIO_IP`
- `MODBUS_PORT`
- `DEVICE_ID`
- `DO_BASE`
- `NUM_DO`
- motor DO channel mapping
- pusher DO channel mapping

### 4. `drivers/modbus_ao.py`

Move to C++: **Yes**

Reasoning:

- AO outputs directly command motor speed.
- This is part of the actual motion loop.

Justification:

- The control loop should not compute wheel outputs in one process and enqueue speed writes to another Python task if we are investing in C++ at all.
- This is one of the clearest places where keeping the full closed path together improves determinism.

Required functionality to preserve:

- convert voltage to DAC register value
- write AO register
- maintain last commanded AO values

Libraries:

- `libmodbus`
- `spdlog`
- `nlohmann/json`

Parameters needed:

- `AO_IP`
- `MODBUS_PORT`
- `DEVICE_ID`
- `AO_BASE`
- `NUM_AO`
- `V_RANGE`
- `DAC_RES`
- `AO_MAX_VOLTAGE`

### 5. `core/pid.py`

Move to C++: **Yes**

Reasoning:

- The PID itself is small, but it is part of the continuous control path.
- Moving only the PID math would not help much unless the surrounding loop also moves.

Justification:

- The correct migration boundary is the whole tape-follow controller, not a partial port of the math alone.
- Keeping the controller state local to the low-level runtime avoids cross-process state sync complexity.

Required functionality to preserve:

- proportional term
- deadband-limited integral
- filtered derivative
- output clamp
- speed reduction filter
- gain switching without state reset
- explicit reset on tape loss, stop, or mode transition

Libraries:

- C++ standard library only

Parameters needed:

- `KP`, `TD`, `N`
- `KP_SLOW`, `TD_SLOW`, `N_SLOW`
- `TI`, `TI_DEADBAND`, `TI_MAX`
- `DT`
- `V_RED_COEF`, `V_RED_COEF_SLOW`
- `OUTPUT_CLAMP_RPM`
- `SR_ALPHA`
- `SR_CAP`

### 6. `modes.py` low-level tape-follow portion (`auto_mode`)

Move to C++: **Partially**

Move the low-level tape-follow execution loop to C++.

Keep the high-level mode orchestration in Python.

Reasoning:

- The specific loop that reads latest sensor data, selects gains, ramps target speed, computes PID output, and emits AO commands is the best C++ candidate in the whole repo.
- The higher-level transitions between `manual`, `armed`, `running`, `reverse`, and `emergency` are still readable and maintainable in Python.

Justification:

- Splitting `modes.py` at the right boundary avoids rewriting healthy orchestration logic.
- Python should request motion mode changes; C++ should execute them.

Move to C++:

- forward/reverse tape-follow control loop
- local ramping
- local loss-of-tape stop
- local CAN timeout stop
- local command timeout stop
- direct drive output generation

Keep in Python:

- system mode transitions
- operator start/reset semantics
- selection of when auto/reverse should start or stop
- sequence-driven speed mode changes

Parameters needed:

- `AUTO_TARGET_HIGH_SPEED`
- `AUTO_TARGET_SLOW_SPEED`
- `AUTO_TARGET_EXTRA_SLOW_SPEED`
- `ACCEL_RATE`
- `DT`
- `SENSOR_ORIENTATION`
- `CAN_TIMEOUT`
- `DI_LIDAR_STOP`
- `DI_LIDAR_SLOW`

### 7. `motion.py`

Move to C++: **Partially**

Reasoning:

- The conversion functions and low-level wheel/relay command implementation should live with `vehicle_core`.
- High-level semantic commands can still be issued by Python over IPC.

Justification:

- Python should say things like `set_manual_command`, `set_auto_mode`, `set_speed_mode`, `brake`, `idle`.
- C++ should translate those into DO/AO writes.

Move to C++:

- `voltage_to_rpm`
- `rpm_to_voltage`
- `mps_to_rpm`
- direct `_drive` implementation
- brake/idle command execution
- pusher relay sequencing

Keep in Python:

- none of the low-level actuation helpers need to remain local once IPC exists

Parameters needed:

- `WHEEL_DIAMETER`
- `GEAR_RATIO`
- `WHEEL_CIRCUMFERENCE`
- `MOTOR_CHANNELS`
- `PUSHER_CHANNELS`
- `AO_MAX_VOLTAGE`

### 8. `safety_watchdog.py`

Move to C++: **Partially**

Reasoning:

- The existing watchdog is not the certified safety layer, but it is a motion-affecting local health guard.
- The low-level stale-driver timeout that forces speed zero belongs in `vehicle_core`.

Justification:

- A local core should stop motion even if Python is blocked or disconnected.

Move to C++:

- command heartbeat timeout
- CAN stale timeout
- DI stale timeout
- low-level force-stop on core-owned hardware faults

Keep in Python:

- high-level system fault display
- dashboard fault reporting
- orchestration decisions after low-level stop

Parameters needed:

- `WATCHDOG_DI_TIMEOUT_S`
- `WATCHDOG_CAN_TIMEOUT_S`
- command heartbeat timeout from Python

### 9. `drivers/rfid_tcp.py`

Move to C++: **No, keep in Python initially**

Reasoning:

- RFID is event-driven and not in the inner motion loop.
- The sequence engine already consumes RFID in Python cleanly.

Justification:

- Moving RFID to C++ adds complexity without much timing benefit.
- It is better to preserve Python’s fast iteration for workflow logic.

Keep in Python:

- socket connection
- tag parsing
- forwarding tags to `SequenceEngine`

Possible future move:

- only if RFID traffic volume, protocol complexity, or reliability needs become much higher

### 10. `core/sequence_engine.py`

Move to C++: **No**

Reasoning:

- This is declarative workflow logic, not hard real-time control.
- It is one of the most flexible parts of the current codebase.

Justification:

- Keeping this in Python preserves fast modification of multi-stop workflows.
- It is already structured in a way that benefits more from high-level expressiveness than from low-level performance.

### 11. `app/app.py`

Move to C++: **No**

Reasoning:

- Dashboard/API logic is not timing critical.
- Python/Flask is already appropriate here.

Justification:

- This is supervisory tooling.
- It should consume status from Python’s in-memory view of system state, which in turn mirrors `vehicle_core` status.

### 12. `drivers/slmp_plc.py`

Move to C++: **No initially**

Reasoning:

- It is not part of the AGV TN active profile.
- It is not in the inner motion loop.

Justification:

- Leave it in Python until the low-level motion migration is stable.
- Revisit only if PLC exchange becomes time-critical or tightly coupled to motion.

## New C++ Program Design

Create a new executable:

- `vehicle_core`

Suggested source layout:

```text
vehicle_core/
  CMakeLists.txt
  include/
    config.hpp
    types.hpp
    ipc_server.hpp
    can_reader.hpp
    modbus_di.hpp
    modbus_do.hpp
    modbus_ao.hpp
    controller.hpp
    watchdog.hpp
    pusher.hpp
    app.hpp
  src/
    main.cpp
    app.cpp
    ipc_server.cpp
    can_reader.cpp
    modbus_di.cpp
    modbus_do.cpp
    modbus_ao.cpp
    controller.cpp
    watchdog.cpp
    pusher.cpp
    config.cpp
```

## Required C++ Libraries

Use the following libraries:

- `CMake`
  - build system
- `nlohmann/json`
  - JSON config parsing and IPC payloads
- `spdlog`
  - logging
- `standalone Asio` or `Boost.Asio`
  - localhost TCP IPC and timers
- `libmodbus`
  - DI/DO/AO over Modbus TCP
- Linux `SocketCAN`
  - CAN I/O

Optional helper libraries:

- `CLI11`
  - command-line flags
- `fmt`
  - formatting if not relying only on `spdlog`

## IPC Contract Between Python and C++

Use localhost TCP JSON messaging.

Recommended default endpoint:

- host: `127.0.0.1`
- port: `8765`

Message framing:

- newline-delimited JSON

Reason:

- easy to inspect manually
- easy for both Python and C++
- enough for current throughput

### Python to C++ commands

Define at least:

- `set_operating_mode`
  - values: `idle`, `manual`, `auto_forward`, `auto_reverse`
- `set_manual_command`
  - values: `forward`, `reverse`, `left`, `right`, `fwd_left`, `fwd_right`, `rvs_left`, `rvs_right`, `idle`
- `set_speed_mode`
  - values: `HIGH`, `SLOW`, `EXTRA_SLOW`
- `set_reverse_auto_request`
  - boolean if still useful after refactor
- `pusher_extend`
- `pusher_retract`
- `pusher_clear`
- `brake`
- `idle`
- `heartbeat`

Example:

```json
{"type":"set_operating_mode","mode":"auto_forward"}
```

```json
{"type":"set_speed_mode","speed_mode":"SLOW"}
```

```json
{"type":"heartbeat","ts":1710000000.0}
```

### C++ to Python status payload

Publish at fixed rate, for example 20 Hz or 10 Hz:

- `mode`
- `fault`
- `fault_detail`
- `latest_di`
- `latest_do`
- `left_mm`
- `right_mm`
- `tape_detected`
- `left_marker`
- `right_marker`
- `sensor_failure`
- `left_rpm_cmd`
- `right_rpm_cmd`
- `left_voltage_cmd`
- `right_voltage_cmd`
- `pid_error`
- `pid_p`
- `pid_i`
- `pid_d`
- `pid_output`
- `speed_mode`
- timestamps for driver freshness

Example:

```json
{
  "type":"status",
  "mode":"auto_forward",
  "fault":false,
  "latest_di":[false,false,true],
  "latest_do":[true,false,false],
  "left_mm":-12,
  "right_mm":17,
  "tape_detected":true,
  "left_marker":false,
  "right_marker":true,
  "left_rpm_cmd":220.0,
  "right_rpm_cmd":245.0,
  "left_voltage_cmd":2.9,
  "right_voltage_cmd":3.1,
  "pid_error":12.0,
  "pid_p":24.0,
  "pid_i":1.1,
  "pid_d":-3.2,
  "pid_output":21.9,
  "speed_mode":"SLOW"
}
```

## Python Changes Required

The Python code should become a supervisor over `vehicle_core`, not the low-level executor.

### Files to update

- `repo/main.py`
- `repo/state.py`
- `repo/modes.py`
- `repo/motion.py`
- `repo/safety_watchdog.py`
- `repo/app/app.py`

### Main Python changes

#### `main.py`

Change from:

- directly instantiating CAN/DI/DO/AO low-level runtime tasks

To:

- starting an IPC client task to `vehicle_core`
- receiving status updates from `vehicle_core`
- sending heartbeats and commands
- retaining RFID and sequence tasks in Python

#### `state.py`

Add fields for:

- `core_connected`
- `core_fault`
- `core_fault_detail`
- `core_last_status_ts`
- `core_mode`
- latest low-level telemetry mirrored from C++

#### `modes.py`

Refactor to:

- issue high-level commands to `vehicle_core`
- no longer compute PID locally
- no longer write AO/DO directly for drive control
- still own high-level transitions and operator semantics

#### `motion.py`

Refactor to either:

- become a thin Python IPC command wrapper

or:

- remove most of it after its helpers move to C++

#### `app/app.py`

Read status from the mirrored Python state populated from C++ telemetry.

## Configuration Strategy

Keep the current JSON profile structure as the single source of truth.

Reason:

- avoids duplicating configuration in Python and C++
- preserves current deployment workflow

Implement a C++ config loader that reads the same profile file shape as `repo/config.py`.

At minimum, the C++ side must read:

- networking
- I/O mapping
- motor channels
- pusher channels
- hardware
- kinematics
- speeds
- PID tuning
- CAN sensor settings
- watchdog settings
- AGV-specific options like `AO_MAX_VOLTAGE`, `SENSOR_ORIENTATION`, `DI_LIDAR_STOP`, `DI_LIDAR_SLOW`

Recommended runtime flags:

- `--profile /path/to/profiles/agv_tn.json`
- `--log-level info`
- `--ipc-port 8765`

## Phased Implementation Plan

### Phase 0: Preserve behavior and define test baseline

Actions:

- document current runtime behavior and expected telemetry fields
- capture normal examples of:
  - manual motion
  - auto tape-follow
  - stop on tape loss
  - stop on CAN timeout
  - pusher cycle
- define acceptance criteria before migration

Reasoning:

- the migration should prove equivalence, not just compile

### Phase 1: Build `vehicle_core` skeleton and IPC

Actions:

- add CMake project
- add logging
- add config loading
- add TCP JSON IPC server
- implement heartbeat timeout
- implement status broadcast

Deliverable:

- Python can connect to `vehicle_core`
- `vehicle_core` can publish a placeholder status payload

Reasoning:

- establish the process boundary first

### Phase 2: Move CAN reader to C++

Actions:

- implement CAN startup
- decode MGS1600 frames
- publish latest sensor status
- add CAN freshness tracking

Deliverable:

- Python receives live sensor data from `vehicle_core`

Reasoning:

- CAN is the cleanest first low-level migration

### Phase 3: Move Modbus DI/DO/AO to C++

Actions:

- implement DI polling
- implement DO writer
- implement AO writer
- publish DO/DI snapshots
- verify DAC scaling matches current Python behavior

Deliverable:

- `vehicle_core` owns the low-level I/O path

Reasoning:

- the motion loop should not span languages once this phase is done

### Phase 4: Move tape-follow controller to C++

Actions:

- port the PID controller
- port speed ramping
- port forward/reverse auto execution loop
- implement stop on tape loss
- implement stop on CAN timeout
- implement handling of `DI_LIDAR_STOP` and `DI_LIDAR_SLOW`
- publish telemetry

Deliverable:

- Python requests `auto_forward` or `auto_reverse`
- C++ executes the closed loop

Reasoning:

- this is the highest-value migration step

### Phase 5: Convert Python `modes.py` into a supervisor

Actions:

- remove local PID execution
- replace low-level movement calls with IPC commands
- preserve current operator and sequence semantics

Deliverable:

- Python still owns business logic, but not the closed-loop control path

Reasoning:

- minimize behavior changes while moving responsibility cleanly

### Phase 6: Port pusher control if needed

Actions:

- implement relay-safe clear-delay-set sequencing in C++
- expose pusher actions over IPC

Reasoning:

- relay sequencing benefits from local ownership
- however, it can be deferred until core motion is stable

### Phase 7: Harden deployment

Actions:

- create `systemd` service for `vehicle_core`
- update Python service dependencies
- ensure restart policies and ordering
- ensure logs are visible through `journalctl`

Deliverable:

- production-style runtime on Ubuntu

## Recommended `systemd` Layout

Use two services.

### `vehicle_core.service`

- starts first
- owns hardware

Suggested service behavior:

- `Restart=always`
- `RestartSec=1`

### `agv_supervisor.service`

- starts after `vehicle_core.service`
- runs Python `main.py`

Suggested ordering:

- `After=vehicle_core.service`
- `Requires=vehicle_core.service`

Reasoning:

- cleaner recovery model
- Python can reconnect if core restarts
- core can stop motion locally even if Python dies

## Acceptance Criteria

The migration is acceptable only if the following are true:

- manual mode behavior remains functionally equivalent
- auto forward and reverse remain functionally equivalent
- tape loss still causes immediate stop/brake behavior
- CAN stale timeout still causes immediate stop behavior
- DI-based stop/slow behavior remains intact
- sequence-triggered speed changes still work
- dashboard still shows meaningful live motion and I/O state
- Python process restart does not leave motion uncontrolled
- loss of Python heartbeat causes `vehicle_core` to stop motion safely

## Explicit Non-Goals for First Migration

Do not add these during the first C++ transition:

- ROS or ROS 2 integration
- full navigation stack
- fleet management
- map-based routing
- full safety-certified redesign
- major sequence engine redesign
- SLMP migration unless needed later

Reasoning:

- the goal is to harden the existing architecture, not replace the product direction

## Summary of What Changes and Why

Change to C++:

- CAN reader
- DI/DO/AO low-level I/O
- PID
- tape-follow control loop
- low-level watchdog/timeouts
- optional pusher low-level sequencing

Reason:

- these are timing-sensitive and hardware-adjacent

Keep in Python:

- mode orchestration
- sequence engine
- RFID workflow integration
- dashboard/API
- high-level configuration flow
- SLMP initially

Reason:

- these are supervisory, flexible, and already well-structured in Python

## First Coding Tasks for the Agent

Implement in this order:

1. Add `vehicle_core/` CMake project with `main.cpp`, config loading, logging, and a TCP JSON IPC server.
2. Add a minimal Python IPC client and mirror core status into `AMRState`.
3. Move CAN reading into C++ and publish decoded sensor status.
4. Move Modbus DI/DO/AO into C++.
5. Port the PID and tape-follow loop into C++.
6. Refactor Python `modes.py` so it sends high-level commands instead of performing the low-level loop.
7. Add `systemd` unit files for both processes.

