# Terminal Wheel-Speed Calibration — Implementation Spec

> **For the coding agent:** you are adding a **standalone, terminal-launched** wheel-speed
> calibration tool to an AGV controller codebase that is architecturally similar to the
> reference codebase but does **not** yet have any wheel-calibration feature. This document
> tells you what to build, why the design is shaped this way, and which existing modules to
> reuse. Read your own codebase's `motion.py`, `config.py`, `state.py`, and the Modbus
> AO/DO driver modules before writing code — this spec names them but you must confirm the
> exact symbols in *your* tree.

---

## 1. Goal

Measure the AGV's **actual wheel ground speed** against the **commanded speed**, so the
per-wheel motor calibration constants (`RPM_PER_VOLT` / `RPM_VOLT_OFFSET`, left and right)
can be fitted. The tool:

1. Drives **both wheels straight** in an open-loop stepped ramp (0 → `V_MAX`, stepping
   `V_STEP` every `DWELL_S`).
2. Simultaneously reads a **CANopen absolute encoder** coupled to one wheel by a friction
   wheel, which reports the true ground speed of that wheel.
3. Logs commanded-vs-actual to **CSV + PNG** for offline fitting.

It is launched from a terminal, **not** the web HMI:

```bash
python3 calibration.py l      # encoder mounted on the LEFT wheel
python3 calibration.py r      # encoder mounted on the RIGHT wheel
```

The wheel argument selects **which wheel's voltage the log attributes the reading to** and
labels the output files. Both wheels are always driven straight; the encoder physically
sits on one of them, and you run the tool twice (once per side).

---

## 2. Measurement principle (the encoder + coupling)

A small **friction wheel** on the encoder shaft is pressed against the AGV drive-wheel rim
(rolling contact). Rim/tangential speeds match at the contact point:

```
omega_enc * r_enc  =  omega_agv * r_agv  =  v_ground
```

Because the AGV wheel rolls on the floor, its rim speed **is** the ground speed. Therefore:

```
v_ground = omega_enc * r_enc          (r_enc = encoder friction-wheel radius)
```

The AGV wheel diameter does **not** enter the linear-speed calculation — only the encoder
friction-wheel radius does. Measure `r_enc` accurately; it scales `v` linearly.

**Caveats to document in the tool's header (they bound accuracy):**
- Any slip at the friction contact **under-reads** speed.
- External contact spins the encoder **opposite** the AGV wheel → a sign flip only (a
  `DIRECTION_SIGN` constant, set so forward driving reads positive).
- The encoder is **multi-turn**: its raw count accumulates across revolutions and wraps
  only at the **total measuring range**, not every revolution. Wrap correction must use the
  total range, not counts-per-rev (see §6).

---

## 3. Why terminal, not HMI — and the systemd constraint

The production controller runs as a **systemd service** (e.g. `agv-controller.service`).
That service **owns the hardware**. The calibration tool needs the **same** motors and the
**same** CAN adapter. This creates two hard resource conflicts:

### 3.1 The CAN adapter is a single, exclusively-locked device
The encoder and the tape sensor (MGS1600) share **one** physical CANable USB adapter
(auto-discovered by USB VID/PID, appearing as `/dev/ttyACM*`). A serial device can be
opened by **exactly one process**. While the controller is running with CAN enabled, it
holds that adapter — a second process **cannot open it at all**.

> In the reference codebase the *in-app* calibration deals with this by stopping the
> MGS1600 CAN driver first to free the shared adapter, then starting the encoder driver,
> then restoring the MGS1600 on exit. A separate terminal process cannot reach into the
> running service to do that hand-off — so it must not be running.

### 3.2 The motor outputs would be double-driven
Motion is written to setpoint tables that an **always-on** `AOWriter`/`DOWriter` re-assert
over Modbus TCP on every change. If the controller service and the calibration process both
write the same AO speed registers and DO direction/brake coils, they **fight over the motor
drives** on a moving machine. The Modbus I/O modules also typically limit concurrent TCP
connections.

### 3.3 Conclusion: **stop the service first**

The calibration tool **must not run concurrently** with the controller. Required flow:

```bash
sudo systemctl stop  agv-controller.service
python3 calibration.py l           # (or r)
sudo systemctl start agv-controller.service
```

**The tool must guard this itself.** On startup it must check whether the controller service
is active and **refuse to run** if so, with a clear message:

```python
# pseudo — confirm the exact unit name in your deployment
active = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE_NAME]).returncode == 0
if active:
    sys.exit("Refusing to run: agv-controller.service is active. "
             "Stop it first:  sudo systemctl stop agv-controller.service")
```

Optionally support a `--manage-service` flag that stops the unit on entry and restarts it in
a `finally` block (requires a passwordless sudoers rule — the reference deployment already
grants one for the HMI restart endpoint, so a similar rule likely exists). **Default to the
safe, explicit behavior: refuse and instruct.** Do not auto-stop the service silently.

---

## 4. Does the tool need the whole software suite? — No.

With the controller stopped, the calibration tool is a **small self-contained asyncio
program** that stands up only the two hardware paths it needs. Reuse the existing modules —
do **not** re-implement the motor math or Modbus framing.

**Include (reuse existing modules):**

| Concern | Reuse | Why |
|---|---|---|
| Config / profile load | `config` | Motor cal constants, channel maps, IPs, ramp params. |
| Motor command math | `motion.py` | `mps_to_rpm`, `rpm_to_voltage` (per-wheel), `set_forward`, `idle`. Keeps calibration measuring exactly what the real controller commands. |
| Shared state / setpoint tables | `AMRState` (`state.py`) | `set_ao`/`set_do` + dirty events that the writer tasks consume. Instantiate one; it's cheap. |
| Analog output | `AOWriter` (`drivers/modbus_ao.py`) | Flushes AO speed setpoints to `AO_IP` over Modbus. |
| Digital output | `DOWriter` (`drivers/modbus_do.py`) | Flushes direction/brake coils to `DIO_IP`. **Required** — without it the direction coils are never set, so the AGV won't drive forward. |
| Encoder input | `EncoderReader` (`drivers/can_encoder.py`) | Owns the (now free) CANable, publishes `encoder_v`/`encoder_rpm`/`encoder_count` onto state. **If your codebase lacks this module, create it per §6.** |

**Required safety input (the emergency stop):**

| Concern | Reuse | Why |
|---|---|---|
| Emergency stop | `DIReader` (`drivers/modbus_di.py`) | The physical E-stop button is wired to the **DIO module** and read as a discrete input. With the controller service stopped, there is **no** watchdog / emergency FSM, so this tool must poll the E-stop itself. **`DIReader` is required, not optional.** |

> **The emergency button is a hardware DI on the DIO module.** In the **new AGV** it is
> **`DI_EMERGENCY = 11`** (the reference unit used index 3 — do not assume; set it explicitly
> in this AGV's `io_mapping`, §8). Confirm the polarity against `DI_FLIPPED`: the reference
> treats E-stop as a normally-open contact where **logical `True` = triggered** after
> `DI_FLIPPED` is applied at read time. Verify on the bench that a physical press reads `True`
> before trusting it.

See §5.1 for the exact behavior on a press.

**Do NOT include:** RFID reader, SLMP/PLC driver, sequence engine, PID controller, MGS1600
tape reader, Flask HMI, `mode_manager`, feature-flag `DriverManager`. None are on the
critical path for "drive straight + measure speed."

---

## 5. Program architecture

A single `async def main()` under `asyncio.run()`:

```
startup
  ├─ parse argv → wheel ∈ {"left","right"}   (accept "l"/"r" and full words)
  ├─ refuse if agv-controller.service is active            (§3.3)
  ├─ setup_logging()  (reuse the project logger if present)
  ├─ state = AMRState()
  ├─ spawn AOWriter.run(state)          # always-on output flushers
  ├─ spawn DOWriter.run(state)
  ├─ spawn DIReader.run(state)          # REQUIRED — emergency stop input (§5.1)
  ├─ spawn EncoderReader.run(state)     # owns the CANable
  └─ wait briefly for state.encoder_connected (e.g. up to 3 s); warn if not linked

ramp loop  (the calibration itself)
  repeat every ~0.05 s until elapsed > total_ramp_time:
    ├─ if emergency_pressed(state) → set estop flag, break     (§5.1)
    ├─ v_cmd = commanded_speed(elapsed)              # staircase, §7
    ├─ rpm   = motion.mps_to_rpm(v_cmd)
    ├─ volt  = motion.rpm_to_voltage(rpm, wheel)     # for logging the encoder's wheel
    ├─ if v_cmd <= 0: await motion.idle(state)
    │  else:          await motion.set_forward(state, rpm)
    ├─ record row: t, v_cmd, volt, encoder_v, encoder_rpm, encoder_count
    └─ print a live status line

cleanup  (finally / asyncio.shield — must always run)
  ├─ await motion.idle(state)           # motors to zero, brakes released
  ├─ cancel EncoderReader, DIReader, AOWriter, DOWriter tasks
  ├─ save CSV + PNG                      # includes all rows up to the E-stop press
  └─ (if --manage-service) restart the controller service
```

### 5.1 Emergency stop behavior

The E-stop is a live abort that **preserves the data collected so far**. Read it from the
shared DI state that `DIReader` publishes each poll:

```python
def emergency_pressed(state):
    di = state.latest_di
    return bool(di) and len(di) > config.DI_EMERGENCY and di[config.DI_EMERGENCY]
    # di[config.DI_EMERGENCY] is already logical (DI_FLIPPED applied by DIReader);
    # True = triggered.
```

On a press, in order:

1. **Break out of the ramp loop immediately** (set an `estop_triggered` flag so the exit
   code / final message reflects it).
2. The `finally`/cleanup block then runs unconditionally and:
   - `await motion.idle(state)` — zeros both AO speed channels and releases direction/brake
     coils so the AGV stops (and can be pushed by hand).
   - **Saves the CSV + PNG containing every row recorded up to the moment of the press** —
     the recorder accumulates rows each loop iteration, so "save whatever the output is at
     during the press" is satisfied by simply saving on exit. Do **not** discard partial data
     on an E-stop; a partial calibration curve is still useful.
   - Logs clearly that the run ended on **EMERGENCY STOP** and prints the saved file paths.
3. **Exit the program** (non-zero exit code, e.g. `2`, to distinguish an E-stop abort from a
   clean completion).

Because `DIReader` polls at `DI_POLL_INTERVAL` (~10 ms) and the ramp loop checks the flag
every ~50 ms, worst-case reaction is well under the dwell step. Keep the physical E-stop the
primary hard stop; this software path guarantees the outputs are zeroed and the data saved.

> **Ctrl-C** must land in the same cleanup path (motors idled, data saved) — it is the manual
> equivalent of the E-stop when the operator can't reach the button.

Key reuse detail: `motion.set_forward` internally sets both wheels forward via
`rpm_to_voltage(rpm, "left")` and `rpm_to_voltage(rpm, "right")`, so **both wheels are
driven with their own per-side calibration** — exactly as in normal operation. The `wheel`
argument only affects which side's voltage you log next to the encoder reading (and the
filename), because the encoder is on one wheel.

`Ctrl-C` (KeyboardInterrupt / CancelledError) must land in the same cleanup path so the
motors are always idled on exit.

---

## 6. The encoder reader (implement if absent)

If your codebase has no CANopen encoder driver, create one. It is a **CANopen DS-406**
absolute encoder read over `slcan` (CANable). Behavior:

**Adapter discovery:** enumerate serial ports, match the CANable by USB VID/PID
(`VID=0x16D0`, `PID=0x117E`), retry if not found.

**Bring-up sequence on connect:**
1. Open the bus: `python-can` `interface="slcan"`, `bitrate=ENCODER_BITRATE` (125000),
   `ttyBaudrate=3_000_000`.
2. Send **NMT start-all**: COB-ID `0x000`, data `[0x01, 0x00]` (Pre-Operational →
   Operational).
3. **(Best-effort) read geometry over SDO** to override config fallbacks — never fatal:
   - counts/rev: expedited SDO upload of object `0x6001` sub `0x00`.
   - total range: object `0x6002` sub `0x00`; the modulo range is `value + 1`.
   - Expedited SDO upload request: COB-ID `0x600 + NODE_ID`, data
     `[0x40, idx_lo, idx_hi, sub, 0,0,0,0]`. Response on `0x580 + NODE_ID`; `data[0]==0x80`
     is an abort; else decode LE int from `data[4:4+n]`.

**Steady-state read:** the encoder streams **TPDO1** on COB-ID `TPDO_COB_ID_BASE + NODE_ID`
(`0x180 + NODE_ID`). Payload is a little-endian `uint32` **absolute count** (object `0x6004`,
multi-turn 24-bit). Per frame:

```
count = struct.unpack_from("<I", msg.data, 0)[0]
delta = count - prev_count
# multi-turn wrap: correct only at the TOTAL range, not per-rev
if   delta >  half_range: delta -= total_range
elif delta < -half_range: delta += total_range
omega     = (delta * rad_per_count) / dt          # rad_per_count = 2π / counts_per_rev
encoder_rpm = omega * 60 / (2π)
encoder_v   = omega * ENCODER_RADIUS_M            # = ground speed
```

Publish `encoder_count`, `encoder_rpm`, `encoder_v`, `encoder_connected`, `encoder_last_rx`
onto the shared state. On `CancelledError`, stop the notifier and `bus.shutdown()` in a
`finally` so the adapter is released cleanly.

> A standalone reference implementation of the same encoder read (direct, no app state) is
> worth mirroring for the framing details: it opens the bus, sends the NMT, reads geometry
> over SDO, then loops decoding TPDO1 with the multi-turn wrap above.

---

## 7. The commanded staircase & ramp bounds

```python
def commanded_speed(elapsed):        # m/s
    n = int(elapsed // DWELL_S)
    return min(V_START + n * V_STEP, V_MAX)
```

Run one extra dwell past reaching `V_MAX` so the top step is captured:

```python
n_steps = max(1, round((V_MAX - V_START) / V_STEP))
total_ramp_time = (n_steps + 1) * DWELL_S
```

Suggested defaults (fit to your straight-track length — the reference uses ~13 m of
straight): `V_START=0.0`, `V_STEP=0.02`, `V_MAX=0.50`, `DWELL_S=2.0`. Sample the loop at
~20 Hz (`asyncio.sleep(0.05)`).

---

## 8. Config additions

Add a calibration + encoder config block (mirror the profile-JSON pattern your codebase uses;
validate at load). Needed constants:

```
# io_mapping — emergency stop DI (NEW AGV wiring)
DI_EMERGENCY = 11        # discrete input index on the DIO module; True = triggered
                         # (reference unit used 3 — this AGV is 11). Confirm DI_FLIPPED polarity.

# encoder
ENCODER_NODE_ID          = 1
ENCODER_BITRATE          = 125000
ENCODER_COUNTS_PER_REV   = 4096          # fallback; SDO 0x6001 overrides
ENCODER_TOTAL_RANGE      = 16777216      # 24-bit; fallback, SDO 0x6002 overrides
ENCODER_WHEEL_DIAMETER_M = 0.060         # friction-wheel contact diameter
ENCODER_RADIUS_M         = ENCODER_WHEEL_DIAMETER_M / 2.0   # the only term in v_ground
ENCODER_TPDO_COB_ID_BASE = 384           # 0x180

# ramp
CAL_V_START = 0.0
CAL_V_STEP  = 0.02
CAL_V_MAX   = 0.50
CAL_DWELL_S = 2.0
```

The motor-command math (`mps_to_rpm`, `rpm_to_voltage`) already reads your existing
`WHEEL_CIRCUMFERENCE`/`GEAR_RATIO` and per-wheel `RPM_PER_VOLT*`/`RPM_VOLT_OFFSET*` — do not
duplicate those.

---

## 9. Output artifacts

Each run creates its **own timestamped folder** under a top-level `_calibration/` directory
(create both if missing). The per-run folder is named:

```
_calibration/calibration_[DD-MM-YYYY]_[HH:MM]/
```

e.g. `_calibration/calibration_09-07-2026_14:35/`. Derive the stamp **once at startup** (not
at save time) so the folder name matches when the run began, and reuse it for the files
inside. Both artifacts go in that folder, wheel-labeled:

- `_calibration/calibration_[DD-MM-YYYY]_[HH:MM]/<wheel>_wheelcal.csv`
- `_calibration/calibration_[DD-MM-YYYY]_[HH:MM]/<wheel>_wheelcal.png`

```python
# derive once at startup
stamp   = datetime.now().strftime("%d-%m-%Y_%H:%M")
run_dir = os.path.join("_calibration", f"calibration_{stamp}")
os.makedirs(run_dir, exist_ok=True)          # created lazily on first save is also fine
```

> **Path note:** the target is Ubuntu, where `:` is a valid filename character, so
> `calibration_09-07-2026_14:35` is fine on the AGV. If the tool is ever run on Windows the
> colon is illegal — if cross-platform matters, substitute `HH-MM`. Follow the `HH:MM` spec
> for the Ubuntu deployment.

CSV columns: `t_s, v_cmd_ms, voltage_v, encoder_v_ms, encoder_rpm, encoder_count`.

PNG: commanded (dashed) vs actual-encoder (solid) speed over time. Use a non-interactive
matplotlib backend (`matplotlib.use("Agg")`), and do the save **off the event loop** (a
short-lived thread) so file I/O never stalls the control loop. Guard the plot in try/except so
a matplotlib failure still leaves the CSV saved.

---

## 10. Operator runbook (put an abridged version in the tool's `--help`)

1. Mechanically couple the encoder friction wheel to the target AGV wheel (start RIGHT).
2. Clear the straight track; keep a hand on the physical E-stop.
3. `sudo systemctl stop agv-controller.service`
4. `python3 calibration.py r`
5. When the tool prints "driving", it ramps automatically; watch the live speed line. The
   **physical E-stop** (or `Ctrl-C`) stops the AGV and **saves the data collected so far** at
   any time.
6. On completion the CSV + PNG are saved to a new
   `_calibration/calibration_[DD-MM-YYYY]_[HH:MM]/` folder for that run.
7. Re-mount the encoder on the LEFT wheel, `python3 calibration.py l`, repeat.
8. `sudo systemctl start agv-controller.service`
9. Fit `rpm = RPM_PER_VOLT·V + RPM_VOLT_OFFSET` per wheel from the logs; update the profile.

---

## 11. Acceptance checklist

- [ ] Refuses to run while the controller service is active; prints the stop instruction.
- [ ] Drives **both** wheels straight; each wheel uses its own per-side voltage calibration.
- [ ] Encoder connects, and `encoder_v` tracks the commanded staircase in the live output.
- [ ] Pressing the E-stop (`DI_EMERGENCY = 11`) aborts the ramp, idles the motors, **saves
      the CSV + PNG with all rows up to the press**, and exits with a non-zero code.
- [ ] E-stop reaction verified on the bench: a physical press reads logical `True` and stops
      the AGV within one dwell step.
- [ ] `Ctrl-C` and normal completion **both** idle the motors, save data, and release the
      CANable.
- [ ] Each run creates its own `_calibration/calibration_[DD-MM-YYYY]_[HH:MM]/` folder holding
      the wheel-labeled CSV + PNG.
- [ ] Restores/leaves the system so `sudo systemctl start agv-controller.service` brings the
      AGV back to normal operation with the CANable free.
- [ ] No RFID / SLMP / PID / sequence-engine / Flask code is pulled in.
```
