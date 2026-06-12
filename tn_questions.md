# AGV TN — Clarification Questions

Questions to close the gap between what the three source documents tell me
(`project_overview.md` = code architecture, `manual.md` = AGV I-PRIME 2.0 operation,
`profiles/agv_tn.json` = this unit's parameters) and the **full** system — mechanical,
electrical, and functional aspects that code and manuals don't always capture.

I've grouped them and noted *why* I'm asking, including the places where the three
sources seem to disagree. Answer in whatever order is easiest; some are quick yes/no
confirmations, others are deeper.

---

## A. Discrepancies between the documents (highest priority — I want to know which is true for TN)

1. **Reverse Auto — removed or live?**
   `project_overview.md §5` states the reverse auto-follow path was *removed* ("Auto is
   forward-only now"), but `manual.md §10.5 / §12.8` document a **START/STOP REVERSE** control
   on the Home page, and the PID comments in the TN profile mention "wheels are forward-only."
   For **AGV TN specifically**: is Reverse Auto present, removed, or planned-but-disabled?
   And does "wheels are forward-only" mean the drive can't spin a wheel backward at all, or
   only that the *PID steering term* can't command negative wheel speed?

2. **"Towing AGV" vs "trolley hook / pusher pin."**
   The TN profile calls it a "base towing AGV," yet the manual describes the pusher as a
   *trolley-hook connect/detach* mechanism, and the KIM variant uses a PLC-driven conveyor
   trolley. On TN, is the pusher pin effectively a **tow-pin / kingpin coupling** that drops
   into a cart's hitch (so "extend = couple, retract = uncouple"), rather than a load pusher?
   I want to be sure I have the mechanical function of the pin right.

3. **"Home" semantics in the tag10 sequences.**
   In `agv_tn.json`, tag `000A` ARRIVAL retracts the pusher (uncouple) + `set_at_home=true`,
   while DEPARTURE extends the pusher (couple) + resumes. So at "home" the AGV *drops* its cart
   and on departure it *picks one up*. Is "home" the **cart pick-up/drop station**, or the
   **parking/charging dock**? And is the at-home latch the only thing distinguishing the two
   identical-tag rules?

## B. Functional safety & emergency behavior (the part code can't prove)

4. **Is the E-stop a hardware safety chain or software-mediated?**
   The manual says E-stop "stops motion"; the code *idles* (releases brakes) via the Ubuntu
   controller reading `DI_EMERGENCY`. If the Ubuntu PC hangs, does an **independent hardware
   relay/contactor** cut motor drive power, or is stopping entirely dependent on software? What
   safety category / performance level (e.g. PLd, Cat 3) is the E-stop circuit rated to?

5. **Brakes: type and fail-safe behavior.**
   Emergency releases the brakes so the AGV can be pushed clear. Are these **spring-applied,
   electrically-released** (fail-safe) brakes, or dynamic/regenerative braking only? On total
   power loss does the AGV brake or roll free — and what happens on any incline on the route?

6. **LiDAR — model and whether the zones are safety-rated.**
   TN exposes three discrete LiDAR DIs (`OUTER=3`, `SLOW/middle=2`, `STOP/inner=1`). Are these
   zone outputs from a **safety-certified scanner** (e.g. SICK/Pilz/Hokuyo with its own OSSD
   zone config), or zones computed in software from raw data? What's the model, and is the
   inner "protective stop" a true safety function or a control-grade convenience stop?

7. **Impact bumper type and coverage.**
   Is `DI_BUMPER` a **safety-rated pressure-sensitive bumper/edge** or a simple mechanical
   switch? Is it front-only? If Reverse Auto is real, what protects the **rear** travel path
   (no rear LiDAR/bumper appears in the profile)?

8. **Single-PC risk.** The control loop *and* the safety watchdog both run on the one Ubuntu
   PC. Is there any watchdog **external** to that PC (hardware timer, safety PLC) that forces a
   stop if the PC freezes mid-motion?

## C. Electrical / drive / power

9. **Drive motors & motor driver.** What are the drive motors (BLDC? brushed? power/torque)?
   The motion layer outputs **0–5 V** analog on a 0–10 V DAC (`ao_max_voltage=5.0`) — what
   controller accepts that 0–5 V throttle, and how is direction commanded (the `do_fwd/do_rev`
   coils)? Can the same controller drive the wheels in reverse, or not (ties to Q1)?

10. **Wheel-speed calibration status for TN.**
    The TN `hardware` block has **no** `RPM_PER_VOLT` / `RPM_VOLT_OFFSET` (AGV1 does). Has TN
    been through wheel-speed calibration yet? If not, what affine mapping is `rpm_to_voltage`
    currently using, and is left/right drift being corrected at all?

11. **Battery chemistry and the "below 48 V" charge threshold.**
    Nominal pack is 48 V / 105 Ah, and the manual says charge when the display reads **below
    48 V**. What chemistry (lead-acid / LiFePO4 / Li-ion)? For most 48 V chemistries "below
    48 V" is already fairly discharged — is that threshold intentional, and is there any
    low-voltage cutoff that protects the pack / forces a stop?

12. **Network wiring on TN.** TN's profile splits I/O oddly: `DIO_IP=192.168.3.30`,
    `AO_IP=192.168.1.30`, `RFID=192.168.3.200`, HMI/`LOCAL_IP=192.168.2.100`. Is the Ubuntu PC
    genuinely **multi-homed across 3 subnets** (.1/.2/.3) for this unit, or are some of these
    placeholders? (The `LOCAL_IP` comment flags it as unverified.) What's the real switch/VLAN
    layout on TN?

## D. Pusher / coupling mechanism (mechanical detail)

13. **Why two DO coils per pusher direction?**
    `pusher_channels` uses `extend=[8,11]`, `retract=[9,10]`. Is that two solenoid valves, two
    actuators, or a redundant pair? And is it a single double-acting **electric linear
    actuator** as the comment says, or pneumatic?

14. **Pusher position feedback.** The sequences move the pusher purely by **timed duration**
    (`duration_s`, 5 s). Is there *any* limit switch / feedback confirming fully
    extended/retracted, or is engagement open-loop and trusted to time alone? If open-loop, how
    is a failed couple/uncouple detected before the AGV drives off?

15. **Horn wiring conflict.** `horn_channels` lists `regular_horn=12`, `alarm_horn=13`, but the
    comment says "horn shared with pusher extend" (extend is DO 8/11). Which is correct? Also
    `alarm_horn=13` (DO) and `DI_BUMPER=13` (DI) share index 13 — I assume that's fine because
    DI/DO are separate address spaces, but please confirm. What distinguishes the **running
    horn** from the **alarm horn** audibly (pattern/continuous)?

## E. Navigation, stations, route

16. **Stop accuracy without a proximity sensor.**
    AGV1 had a `DI_PROX` magnetic station marker for precise stops; TN has **none** — every
    station action is RFID-triggered only. Given read latency + deceleration, how repeatable is
    the stop position, and is that precise enough for the pusher pin to align with a cart's
    hitch? How far **before** the physical station is each tag placed to allow for stopping
    distance?

17. **Tape & RFID physical spec for TN.** What magnetic tape (width/colour/polarity) and which
    RFID tag model / read range are specified for this unit, and at what height/offset are the
    MGS1600 and RFID reader mounted above the floor? These drive real-world reliability and
    aren't in the code.

18. **Tag map completeness.** The profile defines tags `000A/0014/001E/0028/0032` (10/20/30/40/
    50). Is this the **full** route tag set for TN, or a demo subset? Is there a finalized route
    diagram + RFID worksheet (Appendix D) for this unit yet?

## F. Mechanical / load / environment

19. **Chassis & drivetrain layout for the 1-ton tow rating.**
    Differential drive with Ø0.18 m wheels and 30:1 gearing — how many wheels/castors and in
    what layout (centre-drive + corner castors)? What drive-motor torque, and can it start a
    1-ton cart from rest on the worst grade on the route? Top speed is capped ~0.4 m/s in
    config — is that the mechanical limit or just the tuned cap?

20. **Operating environment & duty.** What floor surface, grades/ramps, ambient conditions, and
    pedestrian exposure is TN expected to run in, and what's the real duty cycle behind the
    "1 shift" battery figure (stops per cycle, towing load, distance)? This frames how
    conservative the speed/safety settings need to be.

---

*If any of these are already answered in a doc I haven't been given (route diagram, safety
device diagram, electrical schematic, BOM/Appendix G spares list), just point me to it and I'll
fold it in.*
