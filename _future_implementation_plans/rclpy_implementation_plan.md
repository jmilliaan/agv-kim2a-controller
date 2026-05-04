# ROS 2 Migration Architecture & Implementation Plan
**Project:** KIM2A AGV Controller
**Target:** `rclpy`-based distributed architecture replacing the `asyncio` single-loop + shared memory pattern.
**Context:** This document outlines the step-by-step refactoring required to deprecate `AMRState` and `asyncio.Queue` primitives in favor of standard ROS 2 middleware (Pub/Sub, Services, Actions). The Flask UI remains on a background thread. Fleet management is excluded from this scope.

---

## Phase 1: Sensor Subsystem Migration (Pub/Sub)
**Objective:** Decouple the CAN magnetic guide sensor from the central event loop and deprecate `sensor_queue`.

**Architectural Changes:**
1.  **Define Custom Interface:**
    *   Package: `kim2a_interfaces`
    *   File: `msg/MagneticSensor.msg`
    *   Spec: `int16 left_mm`, `int16 right_mm`, `bool tape_detected`, `bool left_marker`, `bool right_marker`, `bool sensor_failure`.
2.  **Refactor `CANReader` (`drivers/can_mgs1600.py`):**
    *   Convert to an `rclpy.node.Node` named `can_sensor_node`.
    *   Replace `asyncio` polling with an `rclpy.timer.Timer` operating at 50ms (20Hz).
    *   Publish parsed MGS1600 frames to topic `/sensors/magnetic_guide` using the `MagneticSensor` message type.
3.  **Update Auto Mode / PID (`modes.py` & `core/pid.py`):**
    *   Wrap `auto_mode` logic in a `line_follower_node`.
    *   Implement a subscriber to `/sensors/magnetic_guide`.
    *   **Execution:** The PID computation (`compute()` in `PIDController`) must now execute synchronously within the subscription callback, utilizing the incoming message data rather than polling `sensor_queue`.
    *   *Temporary Shim:* For this phase, allow the `line_follower_node` to continue writing directly to `ao_queue` and `do_queue` via `motion.py`.

---

## Phase 2: Actuation & State Distribution
**Objective:** Completely deprecate `state.py` (`AMRState`). Migrate Modbus/SLMP drivers to subscriber nodes and wrap the Flask dashboard in a ROS 2 node.

**Architectural Changes:**
1.  **Define Command Interfaces:**
    *   Implement a custom `msg/WheelRPM.msg` (`float32 left_rpm`, `float32 right_rpm`) or utilize `geometry_msgs/msg/Twist` for kinematic commands.
2.  **Refactor Modbus Actuators (`drivers/modbus_do.py` & `modbus_ao.py`):**
    *   Merge into a single `modbus_io_node`.
    *   **Inputs:** Subscribe to `/cmd/wheel_rpm`.
    *   **Logic:** Move `rpm_to_voltage` and DAC conversion (`target_v / V_RANGE * DAC_RES`) into the subscriber callback. Execute synchronous Modbus writes via `run_in_executor` or standard threading to prevent blocking the executor.
3.  **Refactor Modbus Sensors (`drivers/modbus_di.py`):**
    *   In the `modbus_io_node`, create a 50ms timer to poll DIs.
    *   Publish the state array (applying `config.DI_FLIPPED`) to `/sensors/discrete_inputs` (type `std_msgs/msg/BoolMultiArray`).
4.  **Refactor SLMP PLC (`drivers/slmp_plc.py`):**
    *   Create `plc_slmp_node`.
    *   **Inputs:** Subscribe to `/plc/sequence_request` (type `std_msgs/msg/Int16`).
    *   **Outputs:** Publish M0-M4 and M2040-M2056 states at 20Hz to `/plc/status`.
5.  **Refactor Web Dashboard (`app/app.py`):**
    *   Create `dashboard_node`.
    *   Maintain the Flask app in a daemon thread.
    *   The `dashboard_node` subscribes to `/sensors/magnetic_guide`, `/sensors/discrete_inputs`, and `/plc/status`, caching the latest states in thread-safe local dictionaries.
    *   The Flask `/api/state` endpoint reads exclusively from this cached dictionary.
    *   The Flask `/api/reverse_auto` POST endpoint triggers a publisher on `/cmd/auto_direction` (type `std_msgs/msg/Bool`).

---

## Phase 3: FSM & Sequence Engine (Services & Actions)
**Objective:** Replace `mode_manager` with ROS 2 Services and transition `core/sequence_engine.py` to ROS 2 Actions.

**Architectural Changes:**
1.  **Migrate Mode Management (`modes.py`):**
    *   Create `system_controller_node`. This replaces `mode_manager` and `safety_watchdog`.
    *   Define a custom service `srv/SetMode.srv` (`string requested_mode` -> `bool success`, `string message`).
    *   Expose `/system/set_mode`. Node evaluates transition validity (e.g., "emergency active?") based on cached `/sensors/discrete_inputs` states.
    *   Implement driver health monitoring via standard ROS 2 node lifecycle events or timestamp delta checks on incoming sensor topics. Initiate safety halts by publishing 0.0 RPM to `/cmd/wheel_rpm`.
2.  **Migrate Sequence Engine (`core/sequence_engine.py`):**
    *   Define `action/ExecuteSequence.action`:
        *   Goal: `string sequence_id`
        *   Result: `bool success`
        *   Feedback: `string current_step`, `int16 step_index`
    *   Create `sequence_action_server_node`.
    *   Refactor the JSON-driven parsing logic to map to action states. Replace `asyncio.sleep` with ROS 2 timers and replace queue polling with state checks against subscribed sensor topics.
    *   The action server handles abort/cancel requests natively, ensuring safe deceleration and sequence state clearing.
3.  **Refactor RFID Processing (`drivers/rfid_tcp.py` & `rfid_processor.py`):**
    *   Convert to `rfid_node`. Publish hex strings to `/sensors/rfid`.
    *   Update `system_controller_node` to subscribe to `/sensors/rfid`. Upon receiving a sequence-triggering tag, it instantiates an `ActionClient` and transmits the corresponding goal to the `sequence_action_server_node`.