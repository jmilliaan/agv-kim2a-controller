# Plan: AGV B (TN) Integration into AGV A Codebase

## Context
AGV B is a base towing AGV sharing the same motor/sensor/RFID hardware as AGV A but without SLMP/PLC and trolley integration. It has a double-acting electric linear actuator (extend/retract via DO), a lidar (currently disabled, to be enabled), and a 5V AO ceiling (vs AGV A's 10V). The goal is to make AGV A's clean01 codebase run AGV B without any Python changes — only by switching the profile via `AGV_ID=agv_b_tn`.

Four gaps in the current codebase must be closed first, then a new profile created.

---

## Files to Modify

| File | Change |
|---|---|
| `config.py` | Add 5 new constants with safe defaults |
| `modes.py` | Fix hardcoded 5.0V ceiling; apply sensor orientation; add optional lidar |
| `core/sequence_engine.py` | Add `pusher_extend` and `pusher_retract` action handlers |
| `main.py` | Fix A12: ensure `shutdown()` runs on any unhandled exception |
| `profiles/agv_b_tn.json` | New file: full AGV B profile |

---

## Change 1 — `config.py`: 5 new profile constants

Add after existing constants, with backwards-compatible defaults:

```python
AO_MAX_VOLTAGE   = float(_params.get("ao_max_voltage", 10.0))
SENSOR_ORIENTATION = int(_params.get("sensor_orientation", 1))   # 1=normal, -1=flipped
DI_LIDAR_STOP    = _params.get("io_mapping", {}).get("DI_LIDAR_STOP",  None)  # None = no lidar
DI_LIDAR_SLOW    = _params.get("io_mapping", {}).get("DI_LIDAR_SLOW",  None)
PUSHER_CHANNELS  = _params.get("pusher_channels", None)  # None = no pusher
```

AGV A profile unchanged (all defaults are current AGV A values).

---

## Change 2 — `modes.py`: 3 fixes

**2a. AO voltage ceiling** (line ~162):
```python
# Before:
voltage = max(0.0, min(5.0, motion.rpm_to_voltage(rpm)))
# After:
voltage = max(0.0, min(config.AO_MAX_VOLTAGE, motion.rpm_to_voltage(rpm)))
```

**2b. Sensor orientation** (lines ~32/36 in auto_mode):
```python
# Before:
error_sign = -1.0  # forward
error_sign =  1.0  # reverse
# After:
_orient    = float(config.SENSOR_ORIENTATION)
error_sign = -1.0 * _orient  # forward
error_sign =  1.0 * _orient  # reverse
```

**2c. Optional lidar DI** — add inside `auto_mode` loop, after tape detection checks:
```python
if config.DI_LIDAR_STOP is not None and di and di[config.DI_LIDAR_STOP]:
    # same action as tape_detected == False: brake, reset PID
    ...
if config.DI_LIDAR_SLOW is not None and di and di[config.DI_LIDAR_SLOW]:
    if state.speed_mode == "HIGH":
        state.speed_mode = "SLOW"
```
Only executes if profile defines `DI_LIDAR_STOP`/`DI_LIDAR_SLOW` — AGV A is unaffected.

---

## Change 3 — `core/sequence_engine.py`: pusher actions

Add two built-in action handlers alongside the existing ones. Both read `config.PUSHER_CHANNELS`.

**`_act_pusher_extend`**:
1. Assert `config.PUSHER_CHANNELS` is not None
2. First clear all retract channels (safety: prevent simultaneous extend+retract)
3. Set all extend channels HIGH
4. `await asyncio.sleep(p.get("duration", 2.0))`
5. Set all extend channels LOW

**`_act_pusher_retract`**:
1. Same pattern, reverse channel sets

Channel format in profile:
```json
"pusher_channels": {
  "extend":  [8, 11],
  "retract": [9, 10]
}
```

Register in `__init__`:
```python
self._actions["pusher_extend"]  = self._act_pusher_extend
self._actions["pusher_retract"] = self._act_pusher_retract
```

---

## Change 4 — `main.py`: A12 fix

```python
# Before:
except asyncio.CancelledError:
    logger.info("Main loops cancelled...")
    await shutdown()

# After:
except (asyncio.CancelledError, Exception) as exc:
    if not isinstance(exc, asyncio.CancelledError):
        logger.error("Unhandled task exception — forcing shutdown: %s", exc)
    await shutdown()
```

---

## Change 5 — `profiles/agv_b_tn.json`: New profile

Key differences from `agv1_kim.json`:

```json
{
  "networking": {
    "LOCAL_IP":   "192.168.3.100",
    "DIO_IP":     "192.168.3.30",
    "AO_IP":      "192.168.1.30",
    "RFID_IP":    "192.168.3.200",
    "RFID_PORT":  2022,
    "SLMP_IP":    null,
    "SLMP_PORT":  null
  },
  "features": {
    "DIO_ENABLED":  true,
    "CAN_ENABLED":  true,
    "RFID_ENABLED": true,
    "SLMP_ENABLED": false
  },
  "ao_max_voltage": 5.0,
  "sensor_orientation": 1,
  "io_mapping": {
    "DI_LIDAR_STOP": 13,
    "DI_LIDAR_SLOW": 14
    // ... other DI/DO mappings same as AGV A
  },
  "pusher_channels": {
    "extend":  [8, 11],
    "retract": [9, 10]
  },
  "sequences": [
    // Pusher sequences triggered by RFID — tag hex TBA
    {
      "name": "pusher_extend_seq",
      "trigger": { "type": "rfid", "rfid_tag": "TBD" },
      "actions": [
        { "type": "pusher_extend", "duration": 2.0 },
        { "type": "wait_seconds", "seconds": 1.0 },
        { "type": "pusher_retract", "duration": 2.0 }
      ],
      "cooldown_s": 5.0,
      "requires_mode": "running"
    }
  ]
  // PID, kinematics, speeds, CAN, RFID — same as AGV A
  // motor_channels — same DO[0-5] mapping as AGV A
}
```

> **Placeholders requiring hardware verification before deploy:**
> - `LOCAL_IP` for AGV B's PC
> - `sensor_orientation` — test on hardware with known tape position
> - Pusher RFID tag hex values
> - `DI_MODE_SWITCH` index (currently 3 hardcoded in AGV B's code — verify wiring)
> - PID gains (AGV B uses KP=2.0, TD=0.08; import as starting point but retune on AGV A code)

---

## Verification

1. `AGV_ID=agv1_kim python main.py` — AGV A behavior unchanged, all tests pass
2. `AGV_ID=agv_b_tn python main.py` — starts with SLMP disabled, 5V ceiling, lidar active
3. Manually set `state.speed_mode = "SLOW"` and verify AO does not exceed 5V
4. Trigger a pusher sequence via RFID — verify extend pins HIGH, retract pins never simultaneously HIGH
5. With lidar DI forced HIGH, verify AGV brakes in auto_mode
6. Kill a driver task with an exception — verify `shutdown()` is called and outputs zero

---

## Execution order
1. `config.py`
2. `modes.py`
3. `core/sequence_engine.py`
4. `main.py`
5. `profiles/agv_b_tn.json`
