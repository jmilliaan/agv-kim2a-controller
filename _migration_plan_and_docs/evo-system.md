# AGV Trolley-Dispatch System — System Design (General Architecture)

**Document type:** High-level system design (components + responsibilities + logic).
**Audience:** An LLM (or engineer) with basic AGV knowledge but **no prior context** on this specific system. Everything needed to reason about the system is contained here.
**Scope note:** This is a *general* design that now also embeds the **concrete plant instance** (the 103 Tyre Building floor, 4 loops, 38 machines — see Appendix A). MQTT topic naming and JSON payload schemas live in the companion `evo-system_mqtt_design.md`; detailed per-node finite-state-machine code and exact RFID maps are **deferred** to a later phase.
**Model note:** This revision adopts the **loops** allocation model (an AGV owns several fixed-route **loops**; pairing is **per loop**). The older flat **zone** model (AGV owns TBM 1–20 / 21–38; pair any two in a zone) is **superseded** and no longer used.

---

## 1. One-paragraph summary

A tire factory has **38 machines** (generically called **TBMs** here). The track is **not a single ring** — it is a **branched one-way network of 4 fixed loops** that share a common **spine** through the store area. **2 AGVs** shuttle trolleys between the store area and the TBMs; each AGV **owns 2 of the 4 loops** and runs **one loop per trip**. Each AGV tows **2 trolleys as a train**, so a paired trip ideally serves **2 TBMs**. Empties are **loaded at a shared attach point**; full trolleys are **unloaded at each AGV's own home** (the store area combines the attach point and the two AGV homes). A **store controller** (PC at the store) is the brain: it reads call buttons, pairs calls **within a loop**, dispatches AGVs, drives confirmation LEDs, arbitrates traffic to prevent the two AGVs from colliding on the shared spine, and hosts the operator web app. AGVs follow a **magnetic line** and localize via **RFID tags**. All coordination is over **MQTT** on an isolated local network.

---

## 2. Physical environment & topology

- **Layout:** A **branched one-way network** of **4 fixed loops** (Appendix A). All loops **share a common spine** through the store/attach area, then diverge into their own aisle, and rejoin to return. (Reference floor plan: "103 Tyre Building".)
- **Direction:** All travel is **uni-directional**; **both AGVs travel the same direction** on shared segments. There are no head-on encounters by design.
- **Parallel outbound/inbound lanes:** a loop is **not** one track driven both ways — the **outbound** leg and the **inbound** (return) leg are **separate one-way lanes running parallel**. Combined with **disjoint aisles per AGV** (AGV 1 = Loops 1/2 aisles; AGV 2 = Loops 3/4 aisles), this confines all two-AGV conflicts to the **home/attach/branch convergence area** — see §11.1. The aisles and lanes themselves are conflict-free.
- **Stops are pass-through:** TBM service points, the attach point, the homes, and waiting spots sit **on** the line. An AGV reaches them in normal forward flow. There are **no dead-end spurs** requiring reverse.
- **Overtaking:** Effectively **one lane** within each aisle, so overtaking is only possible in the **store/home area** (each AGV has its own home slot). Elsewhere a trailing AGV cannot pass a leading one.
- **38 TBMs in 3 families:** machines belong to plant families **MRU (4)**, **BTU (24)**, and **STU (10)** = 38. **The families align with loops** (Appendix A), so the family labels are **load-bearing** for allocation — they are not interchangeable generic "TBM 1–38" ids. ("MTU" does not exist on this floor.)
- **RFID stops vs. machines:** A single physical RFID **stop may serve two adjacent machines** (a **shared stop**). **Calling is always per-machine** — 38 independent call sources — even where two share one physical stop point. 38 machines occupy **24 distinct stop positions** (14 of them shared by 2 machines; Appendix A).
- **RFID tag placement:** Tags exist **only at stops and just before junctions/branches** — not continuously. The controller's knowledge of AGV position is therefore **checkpoint-based (coarse)**, updated each time a tag is read.

---

## 3. Core entities & glossary

| Entity | Description |
|---|---|
| **TBM** | A tire-building machine; a demand point. 38 total (MRU/BTU/STU families). Has 6 call buttons + 1 status LED. Needs empty trolleys delivered and full trolleys taken away. |
| **Trolley** | A wheeled cart. **6 types** exist. All share a **common hook**, so the AGV is **trolley-agnostic** (any AGV can tow any type). |
| **Empty trolley** | Delivered *to* a TBM. The **call button selects the empty-trolley TYPE** to deliver. |
| **Full trolley** | Taken *from* a TBM back to the store. Its type is **irrelevant** — not tracked, not matched. |
| **AGV** | Automated guided vehicle. 2 units. Tows **2 trolleys as a train**. Magnetic-line guided, RFID-localized. Has an onboard **confirm button**. **Owns 2 loops**; runs **one loop per trip**. |
| **Loop** | A **fixed, explicitly-routed circuit** owned by exactly one AGV, covering a fixed set of TBMs. It is the **unit of allocation and pairing**: calls pair **only within the same loop**. An AGV owns several loops but **never merges** them (one loop per trip). 4 loops total (Appendix A). |
| **Attach point** | The single **shared load point** where the store MP loads the 2 empties onto the train. Sits **on each loop's route**, in the store area, near both homes. |
| **Home** | A **per-AGV park slot** in the store area; also the **unload point** where the store MP removes the full trolleys at trip end. **2 homes** (one per AGV). |
| **Store area** | The combined **attach + 2 homes** cluster on the floor (the staging area with the two parked AGVs). The store controller, store MP, and the prepare-trolley display live here. |
| **MP (manpower)** | Human operator. Two roles: **store MP** (loads empties at the attach point, removes fulls at the home) and **TBM MP** (swaps empty↔full at the machine). MPs physically attach/detach trolleys and press confirm buttons. |
| **Store controller** | The orchestrator PC at the store. Reads buttons, drives LEDs, pairs/dispatches per loop, arbitrates traffic, hosts MQTT broker + web app. |
| **Train** | The 2-trolley tow configuration: `AGV → FRONT trolley → REAR trolley`. |

---

## 4. Compute & network

| Node | IP | Role |
|---|---|---|
| Store controller | 192.168.2.20 | Orchestrator, MQTT **broker**, operator web app, button/LED I/O, traffic arbiter |
| AGV 1 | 192.168.2.22 | Edge node; owns **Loop 1 (MRU 1–4)** + **Loop 2 (BTU 9–24)** = 20 machines; local Flask monitor |
| AGV 2 | 192.168.2.24 | Edge node; owns **Loop 3 (BTU 1–8 + STU 1–6)** + **Loop 4 (STU 7–10)** = 18 machines; local Flask monitor |
| Wi-Fi AP | 192.168.2.254 | Local wireless; factory-wide coverage assumed |

- All nodes are **Ubuntu 22.04 mini-PCs**, Python-based, with Flask web apps.
- **Isolated /24 network, no internet.**
- **Buttons & LEDs are physically wired to the store controller** (read and driven directly — **not** over MQTT). Only **store↔AGV** coordination uses MQTT.

---

## 5. Trolley carrying model (important for sequencing)

- The AGV always tows **2 trolleys in a fixed train order**: `AGV → FRONT trolley → REAR trolley`.
- **Detach difficulty is asymmetric:**
  - **REAR trolley = easy detach.** Unhook it from the front trolley; done.
  - **FRONT trolley = "sandwiched".** To take it, MP must **unhook AGV→front AND front→rear, then re-hook AGV→rear.** Extra labor.
- **Consequence — loading-order rule:** The **first stop visited** along the route should get the **REAR** trolley (easy detach, no re-hook). The **second stop** gets the **FRONT** trolley.
  - The store controller computes visit order first (§6), then tells the store MP **which type goes front vs. rear** when loading **at the attach point**.
  - Note: a re-hook at the *second* stop can still be unavoidable once the first stop's full trolley occupies the rear position. This is an accepted manual operation, not a controller concern.
- **Type semantics:** Loading is by **type only** (button = empty type). The system does not track full-trolley type on return.

---

## 6. Dispatch logic

### 6.1 Loops (normal mode)
- There are **4 loops**; **each loop is owned by exactly one AGV** (Appendix A). A machine belongs to **exactly one** loop.
- Calls **pair only within the same loop.** Two calls on **different loops never pair** — even if both loops belong to the **same** AGV. An AGV with calls on two of its loops runs them as **separate trips**.
- An AGV runs **one loop per trip** and **never merges loops**.

### 6.2 Per-loop pairing policy
Each loop has a **pairing policy** (Appendix A):
- **Single-immediate (`pair = false`)** — the AGV dispatches **each call immediately** as a **single-trolley trip** (1 empty out, 1 full back). Used for short loops that should not wait (Loops 1 & 4 — MRU, STU 7–10).
- **Paired (`pair = true`)** — the AGV **waits for 2 calls on that loop**, then runs a 2-trolley trip; a lone call goes **single after the single-call timeout** (§6.3). Used for the larger loops (Loops 2 & 3 — the BTU/STU aisles).
- Pairing is **FCFS** by call timestamp: the **first 2 calls** on the loop form the trip. **No override** (no proximity-based or priority re-pairing).
- **One button press = one call = one trolley.** A TBM occupies **one** slot of a trip per call. `trainSize = 2` caps a trip at **2 machines**, so a loop with many pending calls is cleared **2 at a time over multiple trips**.

### 6.3 Single-call timeout (paired loops only)
- On a **paired** loop, if only **1 call** exists and no second call on that loop arrives within **200 seconds**, the AGV **dispatches with a single trolley**. This prevents a lone requester from waiting forever. (Single-immediate loops never wait.)

### 6.4 Visit ordering & loading order
- Visit order = the **loop's authored route order** (the fixed physical sequence the AGV drives; never backtracks).
- Loading order is then derived per §5 (**rear = first stop, front = second stop**) and shown to the store MP for loading **at the attach point**.
- **Shared stops:** if a paired trip's two calls resolve to the **same physical stop** (two machines sharing one stop position), the AGV stops **once** and both empties are delivered in a **single dwell**.

### 6.5 No call cancellation
- Once a button is pressed, the call **cannot be cancelled** (mis-presses are handled manually downstream).

---

## 7. Confirmation LED model (one LED per TBM)

| LED state | Meaning |
|---|---|
| **Off** | No active call for this TBM. |
| **Blinking** | Call accepted — TBM is **queued / waiting for a pair on its loop**, or its AGV is **preparing** (loading at the attach point). (Both pre-dispatch conditions share the blink state.) |
| **Solid on** | The dispatched AGV is **coming** — this TBM is a confirmed stop on an active trip. |
| → Off | Returns to off once the swap is done and the TBM-MP presses confirm (call serviced). |

---

## 8. Human confirmation model

- Each AGV has **one onboard confirm button**. The AGV **will not move** until it is pressed at each gated point. (Failsafe by design — "hard to miss.")
- Confirm is required at:
  1. **Attach point, after loading** the empties → AGV departs to first stop.
  2. **Each TBM, after the swap** (empty removed, full attached) → AGV departs to next stop / back home.
  3. **Home, after unloading** the fulls → AGV parks (idle).
- **Optional alarm:** if confirm is not pressed within **100 s** at a gated stop, raise an alarm on the web app. (The AGV still simply waits; the alarm is advisory.)

---

## 9. Normal operation walkthrough (paired-loop, 2-stop trip)

1. **Call:** TBM-A MP presses one of its 6 buttons (selecting an empty-trolley type). LED → **blinking**. Store controller logs the call (loop, type, timestamp).
2. **Pairing:** A second call appears on the **same loop** (TBM-B), *or* 200 s elapse (→ single-trolley trip). *(On a single-immediate loop, the first call dispatches at once.)*
3. **Plan:** Controller picks the loop's owning AGV, computes **visit order** (route order) and **loading order** (rear = first stop). Both TBM LEDs remain **blinking** (preparing). The **store display** shows the 2 trolley types and which goes front/rear.
4. **Depart + load:** The idle AGV leaves its **home** (empty train) and drives to the **attach point** on its route. The store MP attaches the 2 empties per the displayed order, then presses the **AGV confirm** button.
5. **Dispatch:** AGV proceeds along the loop. Both destination TBM LEDs → **solid on**.
6. **Stop 1:** AGV arrives (RFID match). TBM-A MP removes the empty, attaches the full, presses **AGV confirm**. TBM-A LED → **off**.
7. **Stop 2:** AGV proceeds, arrives at TBM-B. Swap + **confirm**. TBM-B LED → **off**. *(If A and B share a stop, this is one combined dwell.)*
8. **Return:** AGV returns to its **own home**. Store MP removes both full trolleys, presses **confirm**.
9. **Idle:** AGV parks at its **home** — or, if one of its loops already has a dispatchable set, begins the next trip.

Throughout steps 5–8, the **traffic arbiter** (§11) may command this AGV to **hold** to avoid a collision with the other AGV on the shared spine.

---

## 10. Conceptual state models (high-level)

### 10.1 Call lifecycle (per TBM call)
`IDLE → CALLED(queued on loop) → PREPARING(paired/dispatched, loading) → COMING(en route) → SERVICED(swap+confirm) → IDLE`
- No cancel transition exists.

### 10.2 Dispatch/pairing (per loop)
`COLLECTING → (single-immediate: 1 call) OR (paired: 2nd call OR 200 s timeout) → READY_TO_LOAD → LOADING(await attach confirm) → DISPATCHED`
- If the loop's owning AGV is busy (on another trip), a ready dispatch **waits** until the AGV is idle at home.

### 10.3 AGV mission
`IDLE_HOME → DEPART_HOME → AT_ATTACH(load + confirm) → TRAVEL_1 → AT_STOP_1(swap+confirm) → [TRAVEL_2 → AT_STOP_2(swap+confirm)] → RETURN → AT_HOME_UNLOAD(confirm) → IDLE_HOME`
- The bracketed second stop is skipped on single-trolley trips.
- **Direction flag** (`OUTBOUND`/`INBOUND`, §11.2) runs alongside this FSM: it is `OUTBOUND` from `DEPART_HOME`, **flips to `INBOUND` at the swap on the last serviced stop**, and is published with every position report.
- **Overlay states** (can interrupt any TRAVEL/RETURN state): `TRAFFIC_HOLD` (commanded stop, §11) and `FAULT` (§12).

---

## 11. Traffic & collision management (centralized)

The **store controller is the central traffic arbiter.** Junctions are **make-or-break** for this system — a missed conflict is a physical AGV-to-AGV collision — so this section is deliberately detailed. The arbiter is **safe but coarse**: it acts only on RFID tag reads (checkpoint-based, §2), never on continuous position.

### 11.1 Why conflicts are confined to the home/attach area (parallel-lane design)

The track is engineered so that **the only places the two AGVs' paths can meet are the convergence junctions in the home/attach area.** Three design properties guarantee this:

1. **Outbound and inbound are physically separate, parallel lanes.** A loop is *not* one track driven both ways — the outbound leg and the return leg are **distinct one-way lanes** running parallel (e.g. the Loop-1 corridor has an outbound lane and a separate inbound lane). This preserves strict one-way motion and removes head-on and same-lane reverse conflicts along the aisles.
2. **The two AGVs work disjoint aisles.** AGV 1 serves Loops 1 & 2 (the MRU + upper BTU aisles); AGV 2 serves Loops 3 & 4 (the lower BTU/STU aisles). They never share an aisle.
3. **Inbound return trunks are shared only *within* one AGV.** Loops 1 and 2 merge onto one inbound trunk back to home (AGV 1 only); Loops 3 and 4 onto another (AGV 2 only). Each trunk carries a single AGV, so there is no catch-up on it.

**Consequence:** the long aisle and lane stretches are **conflict-free by construction.** The lanes converge only where every trip begins and ends — the **home / attach / branch area** — and that convergence is discretized into the **three junctions** below. The conflict set is therefore **closed at three** for this layout; it must be re-derived if lanes, loops, or homes change (§11.7).

### 11.2 Direction is a tracked AGV state (inbound vs outbound)

Each AGV maintains an **inbound/outbound flag**, defined **relative to home**, and publishes it with every position report:

- **Outbound** = `home -> exit-home -> attach (load) -> branch -> ... -> last serviced machine`.
- **Inbound** = `last serviced machine -> ... -> approach-home -> home`.
- **The flag flips at the trolley swap on the last serviced machine** of the trip (which is the *single* machine on a Loop 1 / Loop 4 single-trolley trip).

Direction is **load-bearing**, not cosmetic: the same physical junction is crossed **outbound on one lane and inbound on the other, using different tags**, and several tags only have meaning in one direction. Every conflict rule keys on **tag + direction**, never the tag alone.

### 11.3 Detection mechanism — tag stream + timing windows (not a static snapshot)

The store **receives every RFID tag read from both AGVs** (the full `pos` stream). A conflict rule has the shape:

> **AGV-x reads trigger tag A, then AGV-y reads trigger tag B within T seconds -> latch a STOP on the lower-priority AGV.**

**Why a timing window and not a static `(tag+dir, tag+dir)` lookup.** A pure snapshot match — "if AGV1's last tag = A and AGV2's last tag = B -> conflict" — is unsafe here because a tag read is *sticky*: an AGV's last-tag stays = A long after it has driven on, parked, and gone idle. A snapshot would fire a **false conflict** (needlessly stopping a clear AGV) whenever the *other* AGV later reaches B, even if the first cleared minutes ago. The **timing window is the real discriminator of co-presence**: only if the second trigger lands within T seconds of the first are both AGVs genuinely in the junction together. (Direction alone does **not** make a crossing safe — two AGVs converging collide regardless of their individual headings; what makes it safe is that one has *already passed*. Timing captures exactly that. Direction instead disambiguates *which* rule applies, since in/out use different tags and lanes.)

Windows (`15 s`, `20 s`, ...) are **tunable configuration**, sized per junction against measured command latency and AGV speed (the lead-distance budget, MQTT §10).

### 11.4 Hold / release — a multi-condition latch

A STOP is a **latch** on the held AGV, carried over `cmd/traffic = stop` and confirmed by the `traffic_hold` **event** — never the ACK (the arbiter treats the AGV as **still moving** until `traffic_hold` arrives). The latch is **cleared by ANY of**:

1. the moving AGV reporting a **designated downstream release tag** (per junction below);
2. **any AGV reporting it is at home** (`AGV n Home` tag) — a coarse "area clear" backstop;
3. a **100 s hard cap** — a temporary fallback so a missed release tag (fault / missed read) cannot hang an AGV forever. *(Provisional; tighten once real tag-read reliability is measured.)*

Multiple release conditions are deliberate **redundancy**. On release the store sends `cmd/traffic = go`. **Priority** (who holds) follows the global rule — the AGV **closer to finishing its route proceeds**, tie -> AGV 1 — and is stated explicitly per junction.

### 11.5 The three junctions

All three live in the home/attach/branch area. Tag ids below are the **decimal RFID ids** from `rfid_mapping.md` (the authoritative tag inventory); the full inbound/outbound tag order is in Appendix B.

#### Junction 1 — concurrent departure (occupancy on the shared exit lane)

- **Geometry:** both AGVs are parked at their homes (`AGV 1 Home = 2`, `AGV 2 Home = 4`, adjacent slots) and both are dispatched. To leave, **both drive the same exit lane** east to the U-turn that wraps to the attach point. The PIZ is that shared exit run.
- **Detection:** both home tags present **and** both AGVs hold a "go" — two simultaneous departures into one lane.
- **Resolution (serialize):** the **first-commanded AGV moves**; the other **holds at its home**. Tie-break (commands effectively simultaneous, sub-millisecond): **AGV 1 goes**, AGV 2 holds.
- **Release:** the moving AGV reads **`Exit home = 6`** (placed past the U-turn, so reading it proves the shared exit run is clear) -> the waiting AGV is released. (Plus the at-home / 100 s latch conditions.)

#### Junction 2 — inbound merge (both returning)

- **Geometry:** both AGVs return toward home on their separate inbound trunks and **merge** just west of the homes. AGV 1 (from Loops 1/2) reads **`Approach home junction from L1 L2 = 230`**; AGV 2 (from Loops 3/4) reads **`Approach home junction from L3 L4 = 430`**. The PIZ is the merge point.
- **Detection:** the **second** approach-junction report (230 or 430, either order) arrives **< 15 s** after the **first**. *(If only one AGV is inbound — no second report inside the window — there is no hold; it proceeds.)*
- **Resolution:** the **second-by-timestamp** AGV is stopped; the first proceeds. Either AGV can be the held one — purely by arrival time, consistent with "closer to finishing proceeds."
- **Release:** the first AGV reads **`Approach home pos = 14`** (far enough past the merge that the second can move without impact). Tag 14 also carries the **final home fork** — AGV 1 forks to its slot, AGV 2 goes straight to its slot.

#### Junction 3 — outbound crosses inbound (AGV 1 out × AGV 2 in)

This junction has **two physical collision points** because AGV 1 can branch to Loop 1 *or* Loop 2; both crossings are covered by one rule.

- **Geometry:** AGV 1 **outbound** (after loading at attach) drives toward Loops 1/2, reading **`Branch: Loop 1 or 2-3 = 12`** as it commits into the branch corridor. AGV 2 **inbound** from Loops 3/4 reads **`Approach home junction from L3 L4 = 430`** on its way home. Their paths **cross**.
- **Detection:** AGV 1 reads **`12`** **< 20 s** after AGV 2 reads **`430`**. *(If AGV 1's `12` report comes later than 20 s, AGV 2 is assumed already clear -> no stop.)*
- **Resolution:** **always stop AGV 1** (AGV 2 is inbound, i.e. closer to finishing -> it proceeds).
- **Release:** AGV 1 is released once AGV 2 reads **`Approach home pos = 14`**, then continues per its destination (Loop 1 vs Loop 2).
- **Mutual exclusion with J2:** AGV 1 is either inbound (a J2 case) or outbound (a J3 case) — **never both at once** — so the shared release tag `14` cannot mis-fire across the two junctions; releasing both held states off one `14` report is intended and safe.

### 11.6 Pre-junction tag placement (latency budget)

Because the arbiter acts only on tag reads, each **trigger tag must sit far enough before its PIZ** that the full `tag-read -> uplink -> evaluate -> stop-command -> downlink -> brake` chain completes before the AGV reaches the conflict point. This lead-distance budget is detailed in **MQTT design §10**; a junction whose geometry cannot provide the required lead distance gets an approach **speed cap** instead.

### 11.7 Extending the conflict set

The three junctions are **complete for the current layout** because of the parallel-lane / disjoint-aisle design (§11.1). **Any change to lanes, loop routes, homes, or the attach point invalidates this closure** and requires re-deriving the junction set from the floor map and tag inventory. New junctions follow the same template: *trigger tag(s) + direction + timing window -> hold the lower-priority AGV -> multi-condition release latch.* The junction rules are **configuration data** (§15), not hard-coded logic.

---

## 12. Degraded & failure modes

| Condition | Behavior |
|---|---|
| **One AGV down/offline** | The down AGV's loops **stall**: their calls **wait** and get **no service** until the AGV returns. The other AGV is **never** reassigned them — **an AGV serves only its own loops, never another AGV's.** The surviving AGV keeps serving **its own** loops normally. With effectively one mover on the shared spine, traffic arbitration degenerates but is harmless. When the downed AGV returns (heartbeat restored), its loops resume. *(Mitigation for the stalled loops is out of scope for this system.)* |
| **Node reboot mid-mission** (AGV or store PC) | **No mission resume.** The system returns to a **safe idle state**; affected calls/missions must be re-initiated. (No hard requirement to persist in-flight mission state — a clean idle on restart is acceptable.) |
| **Obstacle stop** (safety scanner, e.g. NanoScan3) | **Hold → alarm → manual recovery.** |
| **Magnetic line loss** | **Hold → alarm → manual recovery.** |
| **RFID misread / missed tag** | **Hold → alarm → manual recovery.** |
| **Confirm not pressed** at a gated stop | AGV simply waits indefinitely; **optional alarm after 100 s** (§8). |

"Manual recovery" means an operator intervenes (physically and/or via the web app) and clears the fault; the system does not auto-retry.

> **Note on loop ownership:** loop ownership is **strict** — an AGV serves **only its own loops** and never crosses into another AGV's loops, in any mode. When an AGV is down there is **no failover of its loops**; they simply wait. The `single_agv` MQTT mode flag therefore signals only "one AGV is down (traffic arbiter can relax)" — it does **not** reallocate loops.

---

## 13. Communication architecture (high-level)

- **Transport:** MQTT over the isolated LAN. **Broker runs on the store controller (192.168.2.20).** AGVs are MQTT clients.
- **Wired I/O stays local:** call buttons and LEDs are wired to the store controller and handled directly, not published by field devices.

**Conceptual message flows** (topic names/schemas in the companion MQTT doc):

| Direction | Purpose (examples) |
|---|---|
| AGV → Store | Telemetry: current RFID tag, direction, mission state, fault flags; confirm-button events; periodic **heartbeat** (used for liveness / degraded-mode detection). |
| Store → AGV | Commands: trip/mission assignment (loop + ordered stops + loading order), traffic **go/stop**, pause/resume, reset-to-idle. |

- **Latency sensitivity:** traffic stop/go commands and telemetry should be low-latency, since they prevent collisions. (See the MQTT doc §9 latency budget.)

---

## 14. Monitoring & control (web app)

- **Operator app** runs on the **store controller** (Flask). It is **monitor + control** (not read-only):
  - **Monitor:** both AGV positions/states, **per-loop** queues, current dispatch, faults/alarms, mode (dual-AGV vs. single-AGV).
  - **Control:** manual dispatch, pause / E-stop per AGV, resume, reset mission to idle, fault acknowledge / manual-recovery, clear queue.
- **Store-MP prepare display:** the **main page shows the trolleys to prepare for the current dispatch** (the 2 types + front/rear loading order) for loading at the attach point. **Secondary pages** show the fuller picture (pending per-loop queues, per-loop status, etc.).
- **Per-AGV local app:** each AGV also runs its own Flask monitor for that unit.

---

## 15. Configuration data required (deferred, but needed before deployment)

1. **RFID map** — every tag ID mapped to its location (TBM stop / junction / branch / attach / exit-home / approach-home / home), its **valid travel direction(s)** (inbound/outbound), and the per-loop route order. The authoritative inventory is `rfid_mapping.md`; Appendix B cross-references the conflict-relevant tags.
2. **Loop table** — the 4 loops: owning AGV, ordered route, member TBMs, and pairing policy (single-immediate vs. paired). Appendix A is the reference instance.
3. **Shared-stop table** — which TBM pairs are serviced at one physical stop (Appendix A).
4. **Junction conflict rules** — per junction: trigger tag(s) + direction, timing window (s), which AGV holds (priority), and the release-latch conditions (release tag(s) + at-home + 100 s cap). Derived per §11.5; the three current junctions are the reference set. *(This replaces the earlier static `(tag+dir, tag+dir)` lookup — see §11.3 for why a timing model is used.)*
5. **Button→(TBM, trolley type) map** — the 6 buttons per TBM → the 6 trolley types.
6. **MQTT topic hierarchy + payload schemas** — see `evo-system_mqtt_design.md`.
7. **Per-node FSM detail** — full state/transition implementation for store orchestrator, AGV mission (incl. the inbound/outbound flag), call lifecycle.

---

## 16. Scope boundaries & assumptions

**In scope (this document):** overall components, responsibilities, loops dispatch/pairing logic, trolley carrying model, traffic arbitration concept, failure handling, communication architecture (conceptual), the monitoring/control surface, and the concrete plant instance (Appendix A).

**Explicitly out of scope / not part of the system:**
- The "Supermarket" / "QT Trolley Supermarket" blocks on the floor plan — **no role** in this system.
- Hardware design (chassis, drives, sensors wiring) — not addressed here.
- Wrong-trolley poka-yoke — **not implemented**; correctness relies on MP.
- Full-trolley type tracking — **not tracked**.

**Assumptions flagged for confirmation:**
- "Closest to end of route" (traffic priority) is interpreted as **the AGV with fewer remaining stops / nearer to completing its current route proceeds**; tie → AGV 1.
- The store area combines **one shared attach (load) point** and **two per-AGV homes (unload + park)**; the homes let one AGV wait/park while the other is serviced and enable the only overtaking in the system.
- Parallel outbound/inbound lanes + disjoint per-AGV aisles confine **all** two-AGV conflicts to the **home/attach/branch convergence area**, discretized into exactly **three junctions** (§11). This closure holds only for the current layout.
- **Loop ownership is strict and fixed:** an AGV serves only its own loops in every mode. A down AGV's loops **stall** (no failover); the mitigation is out of scope for this system.

---

## Appendix A — Concrete plant instance (103 Tyre Building)

**2 AGVs, 4 loops, 38 machines, 24 stop positions.** All loops share the spine through the store/attach area, diverge into their aisle, then rejoin to return to home.

| Loop | Owning AGV | Pairing | Machines (count) | Stop positions |
|---|---|---|---|---|
| **Loop 1** | AGV 1 | single-immediate | MRU 1–4 (4) | 4 (none shared) |
| **Loop 2** | AGV 1 | paired (200 s) | BTU 9–24 (16) | 8 (each shared by 2) |
| **Loop 3** | AGV 2 | paired (200 s) | BTU 1–8 + STU 1–6 (14) | 8 (6 shared, 2 standalone) |
| **Loop 4** | AGV 2 | single-immediate | STU 7–10 (4) | 4 (none shared) |

- **AGV 1** owns Loops 1 & 2 (20 machines). **AGV 2** owns Loops 3 & 4 (18 machines). Loops 1 and 2 never pair with each other; Loops 3 and 4 never pair with each other.

**Shared stops (two machines, one physical stop position, one dwell when both called):**
- **Loop 2 (BTU 9–24):** BTU9+BTU17, BTU10+BTU18, BTU11+BTU19, BTU12+BTU20, BTU13+BTU21, BTU14+BTU22, BTU15+BTU23, BTU16+BTU24.
- **Loop 3 (BTU 1–8 + STU 1–6):** BTU6+STU6, BTU5+STU5, BTU4+STU4, BTU3+STU3, BTU2+STU2, BTU1+STU1. *(BTU7 and BTU8 are standalone stops.)*

**Visit order (route order the AGV drives, first → second stop):**
- Loop 1: MRU4 → MRU3 → MRU2 → MRU1.
- Loop 2: BTU16 → BTU15 → … → BTU9 (high-to-low at the host stops).
- Loop 3: BTU8 → BTU7 → BTU6 → BTU5 → BTU4 → BTU3 → BTU2 → BTU1.
- Loop 4: STU7 → STU8 → STU9 → STU10.

> The machine-family labels (MRU/BTU/STU) are the operational identity here; the simulation source (`iprime-presentation-tool/_sample_docs/EVO-6F.json`) carries the pixel geometry and the internal node ids (`M-1…M-40`, with `M-5`/`M-6` unused) that map to these names. The simulation's geometric collision handling, its 15 s demo pair-timeout, and its playback speeds are **simulation conveniences** and are **not** part of this design — the real arbiter uses the junction conflict rules (§11) and the 200 s pair timeout (§6.3).

---

## Appendix B — Conflict-relevant RFID tags & travel sequences

Decimal RFID ids are from `rfid_mapping.md` (authoritative full inventory). This appendix lists only the **home/attach/branch** tags that the traffic arbiter reasons about, plus the ordered tag sequences that define the inbound/outbound direction model (§11.2).

**Store-area & junction tags:**

| Dec | Tag | Direction | Role in conflict logic |
|---|---|---|---|
| 2 | AGV 1 Home | — | J1 detect (parked + go); generic "at home" release backstop |
| 4 | AGV 2 Home | — | J1 detect; "at home" release backstop |
| 6 | Exit home | outbound | **J1 release** (read past the exit U-turn → shared exit lane clear) |
| 8 | Attach trolley pos | outbound | load empties (not a conflict trigger) |
| 10 | Branch: loop 1-2-3 or 4 | outbound | route fork {1,2,3}\|{4} (no store-side conflict role) |
| 12 | Branch: loop 1 or 2-3 | outbound | route fork {1}\|{2,3}; **J3 trigger for AGV 1** |
| 200 | Branch: loop 2 or 3 | outbound | route fork {2}\|{3} |
| 14 | Approach home pos | inbound | **J2 & J3 release**; final home fork (AGV1 fork / AGV2 straight) |
| 230 | Approach home junction (from L1 L2) | inbound | **J2 trigger** (AGV 1) |
| 430 | Approach home junction (from L3 L4) | inbound | **J2 trigger** (AGV 2); **J3 trigger** (AGV 2) |
| 120 / 220 | Fork right when inbound, AGV 1 from loop 1 / loop 2 | inbound | merge each loop onto the shared **AGV-1 inbound trunk** (upstream of 230) |
| 320 / 420 | Fork left/right when inbound, AGV 2 from loop 3 / loop 4 | inbound | merge onto the shared **AGV-2 inbound trunk** (upstream of 430) |
| 110 | MRU U-Turn | — | **speed only** (slow for the dead-end U-turn; speed-up trigger = MRU4 tag). Behavioral, not localizing. |
| 500 / 520 | Enter corner half speed / Exit corner full speed | — | **speed only**, one id reused at every corner → **non-localizing**; excluded from position/conflict logic. |

**Direction model — canonical tag order per trip:**

- **Outbound:** `Home (2/4)` → `Exit home (6)` *(J1 release)* → `Attach (8, load)` → `Branch 10` → `Branch 12` *(J3 trigger, AGV 1)* → `Branch 200` → … machine/stop tags … → **last-machine swap = flip to INBOUND**.
- **Inbound:** … last machine … → per-loop fork (`120/220` AGV 1 · `320/420` AGV 2) → shared inbound trunk → `Approach home junction (230 L1/2 · 430 L3/4)` *(J2 trigger; 430 also J3)* → `Approach home pos (14)` *(J2/J3 release + home fork)* → `Home (2/4)`.

> Tag↔AGV home binding is **AGV 1 = tag 2, AGV 2 = tag 4** (authoritative; overrides the `EVO-6F.json` slot ordering). The inbound forks `320`/`420` and `220` are marked *Medium* criticality in `rfid_mapping.md` (gentle merge tracks — a missed fork causes only minor instability), whereas `120` is *Severe* (a hard diverge).
