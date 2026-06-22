# Plan: Remove RFID mapping tool, add module enable/disable, add service-restart button

## Context

Three operator-facing maintenance changes to the AGV controller dashboard:

1. **Remove the RFID mapping tool entirely.** The dashboard ships a full RFID-tag→sequence
   *override editor* (`core/mapping_store.py`, the `/mappings` page, ~10 `/api/mappings/*`
   routes, backups/audit/import-export). It sits on top of the profile's own `sequences`
   block. We're removing the editor + override mechanism; RFID tags still drive motion via
   the profile `sequences` (the RFID reader and the existing "RFID SEQUENCES" runtime toggle
   stay). Decision confirmed: *remove tool, keep sequences*.

2. **Add per-module enable/disable for: digital (DIO), can (motor CAN), safety, horn.**
   `DIO_ENABLED` and `MOTOR_CAN_ENABLED`/`CAN_ENABLED` already exist as boot-time profile
   flags; `safety_watchdog` and `horn_controller` currently *always* run with no flag.
   Because drivers/tasks are instantiated once at boot, these are restart-time flags
   (decision confirmed). A dashboard panel writes the flags to the active profile JSON and
   the restart button (task 3) applies them. Safety is toggleable from the dashboard with a
   warning label (decision confirmed).

3. **Add a "restart service" button** that runs `sudo systemctl restart agv-controller.service`,
   giving operators a one-click way to apply module changes.

---

## Task 1 — Remove the RFID mapping tool

**Delete files**
- `core/mapping_store.py`
- `app/templates/mappings.html`

**`app/app.py`** — remove the `/mappings` page route and all mapping API routes:
`/mappings`, `/api/mappings` (GET + POST), `/api/mappings/apply_now`, `/history`,
`/backups`, `/restore`, `/export`, `/import`, `/unmapped`. Also drop the
`sequences["mapping_reload_pending"] = s.mapping_reload_pending` line (~L75).
- **Keep** `unmapped_rfid_log` and its index usage (`app.py:71`) — that's RFID telemetry,
  not the mapping tool. Only the `/api/mappings/unmapped` *route* goes.

**`main.py:61-70`** — drop the override load. Replace the `load_rules`/`compile_to_sequences`
block with `_initial_seqs = config.SEQUENCES` feeding `SequenceEngine(state, _initial_seqs)`.
`config.SEQUENCES` (config.py:129) already exists as the source of truth.

**`modes.py`** — remove `from core.mapping_store import ...` (L9) and the
`mapping_reload_pending` ARMED-reload block (L634-646).

**`state.py`** — remove `mapping_reload_pending` field (L69) and its shim property (L263-268).

**Templates** — remove the `MAPPINGS` nav button from all 5 templates:
`index.html`, `errors.html`, `manual.html`, `params.html`, `io_monitor.html`.

**Note:** `core/mission.py` only *mentions* mapping-store tags in a comment (no import) —
no code change needed, but update the comment to avoid confusion.

---

## Task 2 — Per-module enable/disable flags

**`config.py`** (near L132-138, the `_feat` block) — add two flags alongside the existing ones:
```python
SAFETY_ENABLED = bool(_feat.get("SAFETY_ENABLED", 1))  # safety watchdog (protective stop)
HORN_ENABLED   = bool(_feat.get("HORN_ENABLED",   1))  # audible horn controller
```

**`main.py`** (task list, L127-129) — gate the always-on tasks:
```python
if config.SAFETY_ENABLED:
    core_tasks.append(safety_watchdog(state, watched=watched))
if config.HORN_ENABLED:
    core_tasks.append(horn_controller(state))
```
Add both to the startup banner loop (L88-94) so the log shows their state.
(`digital`=`DIO_ENABLED` and `can`=`MOTOR_CAN_ENABLED` already gate their drivers here.)

**`profiles/agv-evo-01.json` + `agv-evo-02.json`** — add `"SAFETY_ENABLED": 1` and
`"HORN_ENABLED": 1` to the `features` block; update the `_comment`.

**Dashboard panel** — add a "MODULES" section in `app/templates/params.html` (mirrors the
existing toggle-switch markup at ~L308-337, but these persist + need restart). Four toggles:
Digital (DIO), CAN (motor), Safety (with a ⚠ warning subtitle), Horn. Each writes to the
profile and shows a "restart required to apply" banner.

**Backend** — two new routes in `app/app.py`:
- `GET /api/modules` → returns current four flag values (read from `config` / re-read profile).
- `POST /api/modules` → validates `{feature, enabled}` against an allow-list
  `{"DIO_ENABLED","MOTOR_CAN_ENABLED","SAFETY_ENABLED","HORN_ENABLED"}`, writes the value into
  `profiles/{_config.AGV_ID}.json` `features` (load JSON, set key, dump with indent), logs via
  `_state.log_event`, returns `{ok, restart_required: true}`.
  - Map UI "CAN" → `MOTOR_CAN_ENABLED` (the master CAN flag; `CAN_ENABLED` sensor follows it
    per the existing `can_sensor_on` rule in main.py:79-81).
  - Keep this *separate* from `/api/tuning/toggle` (that one is in-memory soft-disable; these
    are persisted boot flags).

---

## Task 3 — Restart-service button

**Backend** — `POST /api/service/restart` in `app/app.py`:
```python
subprocess.Popen(["sudo", "systemctl", "restart", "agv-controller.service"])
```
Use `Popen` (fire-and-forget) — a blocking call would be killed mid-request when the service
restarts. Log the event first. Follows the existing `subprocess` usage pattern (`api_wifi`).

**Sudoers** — the service runs as `gvipc-06`, so passwordless sudo is required for that one
command. Add to setup docs (and create on the box):
```
gvipc-06 ALL=(root) NOPASSWD: /usr/bin/systemctl restart agv-controller.service
```
via `sudo visudo -f /etc/sudoers.d/agv-controller`.

**UI** — a "RESTART SERVICE" button in `params.html` (near the MODULES panel), with a
JS `confirm()` guard, that POSTs to `/api/service/restart` and shows a "restarting…"
state. After ~5 s the page reconnects when the dashboard comes back.

**Docs** — add a short subsection to `_setup_guide/02_systemd_service.md` documenting the
sudoers entry and that the dashboard MODULES toggles require a restart to take effect.

---

## Verification

1. **Bench run** (no hardware): `AGV_ID=agv-evo-01 python3 main.py`, open `http://localhost:5000`.
   - Confirm no `MAPPINGS` nav button anywhere and `/mappings` + `/api/mappings/*` return 404.
   - Confirm the app still boots and the engine loads sequences from the profile (log line
     "using profile sequences"). RFID "RFID SEQUENCES" toggle on Parameters still works.
2. `grep -rn "mapping_store\|mapping_reload_pending\|/api/mappings" .` → only comments/docs left.
3. **Module flags:** set `SAFETY_ENABLED`/`HORN_ENABLED`/`DIO_ENABLED` to 0 in the profile,
   restart, confirm the startup banner shows DISABLED and those tasks don't run. Toggle each
   from the Parameters MODULES panel and confirm the profile JSON updates + "restart required".
4. **Restart button:** with the sudoers entry installed and running under systemd, click
   RESTART SERVICE → `journalctl -u agv-controller -f` shows a clean stop/start; dashboard
   returns. Without the sudoers entry, confirm the endpoint fails gracefully (logged error).
5. Run existing tests: `python3 -m pytest tests/ -q`.
