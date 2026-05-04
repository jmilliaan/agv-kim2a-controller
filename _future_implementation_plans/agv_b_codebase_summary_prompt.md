You are analyzing the codebase of AGV B — a base towing AGV (no trolley, no SLMP/PLC integration, has an active pin actuator). A separate AGV A codebase will compare against this summary to identify deltas. Your job is to produce a structured, token-efficient summary saved as `agv_b_summary.md` in the repo root.

Do NOT explain concepts. Do NOT add prose. Be terse and factual. Use tables and lists.

---

## Instructions

Read the entire codebase, then write `agv_b_summary.md` with the following sections in order:

---

### 1. FILE TREE
List every `.py` and `.json` file with one-line description. Format:
```
path/file.ext — what it does
```

---

### 2. ENTRY POINT & TASK LIST
- Entry point file and command to run
- List every coroutine/thread launched at startup, in order
- Note if any task is conditional / feature-flagged

---

### 3. STATE OBJECT
- Class name and file
- Every attribute: name, type, default value, owner (which task writes it)
- If there are sub-objects or domains, list them separately

---

### 4. HARDWARE DRIVERS
For each driver (input and output):
- File, class name (if any), protocol (Modbus/CAN/TCP/serial/etc.)
- IP address and port (or serial config)
- Poll rate / trigger method
- What it writes to state

---

### 5. DIGITAL I/O MAPPING
Two tables:

**Digital Inputs (DI)**
| Index | Name/Role | Used by |

**Digital Outputs (DO)**
| Index | Name/Role | Active condition |

Include the active pin (push/retract) explicitly with its DO index.

---

### 6. MOTION & KINEMATICS
- File containing motion primitives
- List every motion function: name, effect, DO/AO channels used
- AO channel mapping (which channel = left/right wheel speed)
- Conversion formula: RPM ↔ voltage (or speed ↔ voltage) if present

---

### 7. MODE / STATE MACHINE
- File containing the main control loop
- List every mode/state by name
- Transition table: from → to, trigger condition
- How emergency is detected and cleared

---

### 8. RFID / SEQUENCE HANDLING
- How RFID tags are read (file, protocol, frame format)
- Tag → command/action mapping (file or hardcoded)
- List every defined tag and its action
- Is there a sequence engine or is logic hardcoded? Describe structure.
- Mention active pin if involved in any sequence

---

### 9. PID / TAPE FOLLOWING
- File and function/class name
- Gains and tuning parameters (names + current values)
- Error signal definition (sensor PV, setpoint)
- Derivative filter: yes/no, implementation
- Integral deadband/clamp: values
- Speed reduction logic: yes/no, formula
- Forward vs reverse differences (error sign, gains, fixed/variable speed)

---

### 10. CONFIGURATION / PARAMETERS
- Config file(s): path, format (JSON/YAML/py constants)
- How config is loaded (direct import, json.load, env var, etc.)
- List every top-level key/section in the config file
- Note any per-AGV profile or multi-AGV support (or absence of it)

---

### 11. WEB DASHBOARD (if present)
- Framework, port, template files
- List every API endpoint: method, path, what it does
- List every UI control visible to operator

---

### 12. MISSING vs AGV A FEATURE SET
Based on what you read, explicitly state which of the following are present or absent:
- SLMP / PLC integration
- Trolley sequences
- Sequence engine (declarative JSON sequences)
- Safety watchdog (driver health monitoring)
- Typed state domains (sub-objects)
- AGV profile system (per-AGV JSON)
- Motor channel parameterization (from config, not hardcoded)
- Reverse auto mode
- Active pin control (push/retract)

---

### 13. NOTABLE PATTERNS & DEVIATIONS
- Any architectural pattern that differs from a standard asyncio producer-consumer model
- Hardcoded values that should be config (IPs, channel numbers, timing constants)
- Copy-pasted logic blocks (e.g. duplicated PID reset, duplicated mode handling)
- Any global mutable state outside the state object
- Error handling approach (retry logic, exception scope)

---

Save the output to `agv_b_summary.md` in the repo root. Do not print to terminal.
