# AGV B (TN) Codebase Summary
> Compare target: AGV A (KIM2A). IPs overridden: DIO_IP=192.168.3.30, RFID_IP=192.168.3.200.

---

## tree

```
parameters.json        — all runtime parameters, single file, flat JSON
src/config.py          — loads parameters.json at import, exposes flat module-level constants
src/state.py           — AMRState class: 5 queues + billboard vars + event log
src/io_hardware.py     — all async hardware drivers: di_reader, rfid_reader, can_reader, do_writer, ao_writer
src/motion.py          — kinematic math + 10 async motion command functions
src/main.py            — asyncio entry point, signal handlers, shutdown routine
src/modes.py           — auto_mode (PID+tape), manual_mode (pendant), mode_manager (DI[3] toggle)
src_testing/test_dio.py       — standalone pusher DO test (not imported by src/)
src_testing/dio_ao_test.py    — standalone DIO+AO combined test
src_testing/test_analog.py    — standalone AO test
src_testing/movement_testing.py — standalone motion sequence test with all drivers
```

---

## entry

| field | value |
|---|---|
| cmd | `python src/main.py` |
| coroutines | `di_reader`, `rfid_reader`, `can_reader`, `do_writer`, `ao_writer`, `mode_manager` |
| gather flags | no `return_exceptions=True` — any driver exception cancels all tasks |
| shutdown | `asyncio.CancelledError` → `shutdown()` zeros all DO/AO via direct Modbus (bypasses queues) |

---

## state

**Class:** `AMRState` — [src/state.py](src/state.py)

| attr | type | default | owner (writer → reader) |
|---|---|---|---|
| `di_queue` | `asyncio.Queue` | empty | `di_reader` → `manual_mode` |
| `rfid_queue` | `asyncio.Queue` | empty | `rfid_reader` → **unconsumed** (no handler exists) |
| `sensor_queue` | `asyncio.Queue` | empty | `can_reader` → `auto_mode` |
| `do_queue` | `asyncio.Queue` | empty | `motion._drive` / `set_brake` / `idle` → `do_writer` |
| `ao_queue` | `asyncio.Queue` | empty | `motion._drive` / `auto_mode` → `ao_writer` |
| `latest_di` | `list[bool] \| None` | `None` | `di_reader` → `mode_manager` |
| `can_last_rx` | `float` | `0.0` | `can_reader` → `auto_mode` CAN timeout check |
| `gui_auto_enabled` | `bool` | `False` | unused (GUI commented out) |
| `last_ao_voltages` | `list[float]` | `[0.0, 0.0]` | `motion._drive` (billboard only) |
| `last_do_states` | `list[bool]` | `[False]*6` | `motion._drive` (billboard only) |
| `last_sensor` | `dict \| None` | `None` | `can_reader` (billboard only) |
| `last_rfid_tag` | `str \| None` | `None` | **never written** |
| `rfid_history` | `list` | `[]` | **never written** |
| `event_log` | `list[dict]` | `[]` | `log_event()` — capped at 500, never called in src/ |

> AGV A diff: KIM2A uses typed dataclass/attrs state with explicit field types. TN uses plain class with no annotations.

---

## drivers

| file | fn | protocol | address | poll | writes_to |
|---|---|---|---|---|---|
| io_hardware.py | `di_reader` | Modbus TCP | 192.168.3.30:502 | 0.05 s sleep | `state.latest_di`, `state.di_queue` |
| io_hardware.py | `rfid_reader` | raw TCP | 192.168.3.200:2022 | event-driven recv | `state.rfid_queue` |
| io_hardware.py | `can_reader` | CAN slcan | USB auto-detect VID=0x16D0 PID=0x117E, 500 kbps, ttyBaud=3 000 000 | event-driven async-for | `state.last_sensor`, `state.sensor_queue`, `state.can_last_rx` |
| io_hardware.py | `do_writer` | Modbus TCP | 192.168.3.30:502 | queue-driven | DIO coils (DO_BASE + ch) |
| io_hardware.py | `ao_writer` | Modbus TCP | 192.168.1.30:502 | queue-driven | AO registers (AO_BASE + ch) |

> AGV A diff: KIM2A wraps each driver in a `SensorDriver`/`ActuatorDriver` ABC class with `setup()`/`read()`/`write()` methods. TN uses bare async functions.

---

## dio

**DI table** — base=0, count=16, device_id=1, module IP=192.168.3.30

| idx | name | used_by |
|---|---|---|
| 3 | mode_switch | `mode_manager`: True→manual, False→auto |
| 4 | DI_FWD | `manual_mode` pb_fwd (active-low: False=pressed) |
| 5 | DI_REV | `manual_mode` pb_rvs (active-low) |
| 6 | DI_LEFT | `manual_mode` pb_left (active-low) |
| 7 | DI_RIGHT | `manual_mode` pb_right (active-low) |
| 10 | DI_EMERGENCY | `manual_mode` emergency (NC: False=triggered) |
| 13 | DI_LIDAR_INNER | `auto_mode` lidar_stop — **hardcoded False, disabled** |
| 14 | DI_LIDAR_MIDDLE | `auto_mode` lidar_slow — **hardcoded False, disabled** |
| 0–2, 8–9, 11–12, 15 | — | unused |

**DO table** — base=0, count=16, device_id=1, module IP=192.168.3.30

| idx | name | active_cond |
|---|---|---|
| 0 | left_fwd | left wheel forward direction |
| 1 | left_rev | left wheel reverse (`not left_fwd`) |
| 2 | left_brake | True only in `set_brake()`, False in `_drive()` |
| 3 | right_fwd | right wheel forward direction |
| 4 | right_rev | right wheel reverse (`not right_fwd`) |
| 5 | right_brake | True only in `set_brake()`, False in `_drive()` |
| 8 | pusher_extend_A (Y10) | `pusher_up()` in test_dio.py — **not in src/** |
| 9 | pusher_retract_A (Y11) | `pusher_down()` in test_dio.py — **not in src/** |
| 10 | pusher_retract_B (Y12) | `pusher_down()` in test_dio.py — **not in src/** |
| 11 | pusher_extend_B (Y13) | `pusher_up()` in test_dio.py — **not in src/** |
| 6–7, 12–15 | — | unused |

**active_pin:** DO[2] (left_brake) and DO[5] (right_brake) — set True only in `set_brake()`. Never True during directional motion (`_drive()` always writes False to these).

---

## motion

**File:** [src/motion.py](src/motion.py)

| func | effect | DO channels | AO channels |
|---|---|---|---|
| `_drive(state, left_fwd, left_v, right_fwd, right_v)` | core primitive; all others delegate here | DO[0–5] | AO[0–1] |
| `set_forward(state, v)` | both wheels fwd, equal v | 0=T,1=F,2=F,3=T,4=F,5=F | [v, v] |
| `set_reverse(state, v)` | both wheels rev, equal v | 0=F,1=T,2=F,3=F,4=T,5=F | [v, v] |
| `set_left(state, v)` | spin left: L=rev, R=fwd | 0=F,1=T,2=F,3=T,4=F,5=F | [v, v] |
| `set_right(state, v)` | spin right: L=fwd, R=rev | 0=T,1=F,2=F,3=F,4=T,5=F | [v, v] |
| `set_forward_left(state, v_fast, v_slow)` | fwd curve left: L=v_slow, R=v_fast | both fwd | [v_slow, v_fast] |
| `set_forward_right(state, v_fast, v_slow)` | fwd curve right: L=v_fast, R=v_slow | both fwd | [v_fast, v_slow] |
| `set_reverse_left(state, v_fast, v_slow)` | rev curve left: L=v_slow, R=v_fast | both rev | [v_slow, v_fast] |
| `set_reverse_right(state, v_fast, v_slow)` | rev curve right: L=v_fast, R=v_slow | both rev | [v_fast, v_slow] |
| `set_brake(state)` | DO[2,5]=True, AO=0 | 0=F,1=F,2=T,3=F,4=F,5=T | [0, 0] |
| `idle(state)` | all DO False, AO=0 | all False | [0, 0] |

**AO map:** AO[0] = left wheel, AO[1] = right wheel

**RPM ↔ voltage formula:**
```
rpm_to_voltage:  v = (rpm + 101.2) / 646.59
voltage_to_rpm:  rpm = 646.59 * v − 101.2
mps_to_rpm:      motor_rpm = (v_mps × 60 / WHEEL_CIRCUMFERENCE) × GEAR_RATIO
                 WHEEL_CIRCUMFERENCE = π × 0.18 = 0.5655 m
                 GEAR_RATIO = 30
```

> AGV A diff: KIM2A may use different sign convention for left/right PID output application — verify on hardware before migrating.

---

## fsm

**File:** [src/modes.py](src/modes.py)

**States:** `manual`, `auto`

| from | to | trigger |
|---|---|---|
| any | `manual` | `DI[3] == True` |
| any | `auto` | `DI[3] == False` |

**On transition:** cancel active task → flush do_queue + ao_queue → `idle()` → create new task

**Sub-states in auto_mode (not explicit FSM):**

| condition | action |
|---|---|
| `lidar_stop == True` | `set_brake()`, reset PID state, `tape_was_lost=True` |
| `sensor["tape_detected"] == False` | `set_brake()`, reset PID state, `tape_was_lost=True` |
| tape reacquired | `set_forward(0.0)`, `tape_was_lost=False` |
| `time.time() − can_last_rx > CAN_TIMEOUT (0.5s)` | `set_brake()`, reset PID state |

**Emergency:** `manual_mode` reads `DI[10]` NC — `False` triggers `set_brake()`. No emergency handling in `auto_mode` beyond tape_loss/CAN_timeout. No global E-stop FSM.

> AGV A diff: KIM2A has a formal 6-state FSM (IDLE/MANUAL/AUTO/ESTOP/FAULT/SHUTDOWN). TN has 2-state toggle with inline sub-conditions.

---

## rfid

| field | value |
|---|---|
| read_method | raw TCP socket, recv 1024 bytes, `binascii.hexlify`, split on `"CF"`, `tag = packet[28:34]` (6-char hex) |
| init_cmd | `bytes.fromhex("CFFF0070002415")` sent on connect |
| reconnect | outer while loop, sleep 2s on exception |
| tag_map | not implemented — rfid_queue consumed by no coroutine |
| tags (planned) | `10`=home/stop, `20`=retract+wait+resume, `30`=extend+wait+resume — hex values TBD from hardware scan |
| seq_engine | **no** |
| pin_involvement | none |

> AGV A diff: KIM2A parses `packet[26:30]` (4-char hex). TN uses `[28:34]` (6-char). Different hardware — do not change without re-scanning tags.
> AGV A diff: KIM2A has declarative JSON seq_engine with `rfid`/`marker`/`rfid_then_marker` trigger types. TN has none.

---

## pid

| field | value |
|---|---|
| file | [src/modes.py](src/modes.py) — inline in `auto_mode`, not a class |
| KP | 2.0 |
| TI | `null` — I-term disabled (`i_term = 0.0`) |
| TD | 0.08 |
| N | 20 |
| DT | 0.01 s |
| error_def | `e = 0.0 − sensor["left_mm"]` (setpoint=0 mm, PV=left lateral offset) |
| filter | first-order low-pass on D: `alpha = DT / ((TD/N) + DT)`, `filtered_d += alpha × (raw_d − filtered_d)` |
| deadband | none |
| speed_reduction | `abs(e × V_RED_COEF) + abs((pv − last_pv)/DT) × 0.5`, capped at 60% of `base_rpm` |
| fwd_rev_diff | `left_rpm = base_rpm − speed_reduction − output` / `right_rpm = base_rpm − speed_reduction + output` |
| output_clamp | ±870 RPM |
| AO write | directly to `state.ao_queue` (bypasses `motion._drive`) |

> AGV A diff: KIM2A wraps PID in `PIDController` class with `compute()` method. TN is inline. Gain sign convention may differ — verify on hardware.

---

## config

| field | value |
|---|---|
| file | [src/config.py](src/config.py) |
| load_method | `json.load` at module import from `../parameters.json` (relative to src/); raises on missing/malformed |
| top_keys | `networking`, `io_mapping`, `hardware`, `kinematics`, `speeds`, `pid_tuning`, `can_sensor`, `rfid` |
| per_agv | **no** — single `parameters.json`, no profile system, no `AGV_ID` env var |
| derived | `WHEEL_CIRCUMFERENCE = 3.14159 × WHEEL_DIAMETER` computed in config.py |
| RFID_INIT_CMD | decoded via `bytes.fromhex()` at import time |

> AGV A diff: KIM2A uses `profiles/{AGV_ID}.json` with env var profile selection. TN has no per-AGV config layer — planned in integration Phase 1.

---

## web

| field | value |
|---|---|
| framework | **none** — Flask thread commented out in `main.py:39-42` |
| port | n/a |
| endpoints | none |
| ui_controls | none — `state.gui_auto_enabled` set False, never read |

> AGV A diff: KIM2A has Flask dashboard with live state endpoints and UI controls.

---

## features

| feature | TN (AGV B) | AGV A (KIM2A) |
|---|---|---|
| SLMP | no | yes |
| pusher/trolley | test code only (`src_testing/`), not in `src/` | conveyor via SLMP |
| seq_engine | no | yes — declarative JSON, rfid/marker triggers |
| safety_watchdog | no dedicated task; CAN timeout check inline in auto_mode | yes — `safety_watchdog` coroutine |
| typed_state | no — plain class, no type annotations | yes — typed dataclass/attrs |
| per_agv_config | no — single parameters.json | yes — `profiles/{AGV_ID}.json` |
| motor_params | yes — WHEEL_DIAMETER, GEAR_RATIO, V_RANGE, DAC_RES, speed params | yes |
| reverse_auto | no — auto_mode is forward-only | yes |
| active_pin | DO[2,5] brake pins (set True only in `set_brake()`) | verify |

---

## patterns

**arch_deviations**
- All IO drivers are bare async functions, not classes — no ABC, no `setup()`/`read()`/`write()` lifecycle
- PID is inline in `auto_mode`, not a reusable class
- No per-AGV profile system; no `AGV_ID` env var
- `rfid_queue` is written but never consumed — `state.last_rfid_tag` and `state.rfid_history` are never updated
- Pusher control (DO[8–11]) exists only in `src_testing/`, not wired into `src/`
- Flask GUI thread is present but fully commented out

**hardcoded_values**
- `lidar_stop = False` [modes.py:29](src/modes.py#L29) — debugging override bypasses `DI[13]`
- `lidar_slow = False` [modes.py:32](src/modes.py#L32) — debugging override bypasses `DI[14]`
- `DI[3]` raw index for mode switch — not a named constant in parameters.json or config.py
- `5.0` voltage ceiling in `auto_mode` AO clamp — not a config constant

**duplicated_logic**
- Pusher illegal-combination guard (`ch08&ch10`, `ch09&ch11`, etc.) duplicated verbatim in `test_dio.py` and `dio_ao_test.py`
- Modbus reconnect pattern repeated across `di_reader`, `do_writer`, `ao_writer` with slight style differences

**global_mutables**
- None — all mutable state lives in `AMRState` instance, passed by reference to every coroutine

**error_handling**
- `di_reader`: `try/except Exception` → `client.close()` + `sleep 2s`, loops
- `rfid_reader`: `try/except Exception` wraps entire connect+recv loop; `finally: sock.close()`; `sleep 2s`
- `can_reader`: `try/except Exception`; `finally: notifier.stop() + bus.shutdown()`; `sleep 2s`
- `do_writer`/`ao_writer`: `try/except Exception` → `client.close()` + `sleep 1s`; **command is dropped on failure** (no retry, no re-queue)
- `shutdown()`: bare `try/except Exception`, prints error, continues — non-fatal
- No `return_exceptions=True` on `asyncio.gather` — any unhandled exception from any task cancels all others
