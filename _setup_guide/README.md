# AGV EVO controller — deployment setup guides

Three short guides for bringing a fresh Ubuntu PC (the on-vehicle controller) up to a
hands-off boot: power on → controller running → dashboard on screen.

| # | Guide | What it does |
|---|-------|--------------|
| 1 | [01_agv_id_env_var.md](01_agv_id_env_var.md) | Make `AGV_ID` permanent so the controller always loads the right profile |
| 2 | [02_systemd_service.md](02_systemd_service.md) | Run the controller as a `systemd` service (auto-start on boot, auto-restart on crash) |
| 3 | [03_kiosk_display.md](03_kiosk_display.md) | Make the XFCE desktop auto-open the dashboard full-screen on the attached screen |

**Assumptions (change to match your unit):**

| Thing | Value used in the guides |
|-------|--------------------------|
| Login user | `gvipc-06` |
| Project path | `/home/gvipc-06/agv-kim2a-controller` |
| Profile / unit id | `agv-evo-01` (use `agv-evo-02` on the second unit) |
| Python | system `python3` (3.10+); swap in a venv path if you use one |
| Dashboard URL | `http://localhost:5000` (Flask binds `0.0.0.0:5000`) |
| Desktop | XFCE with autologin already configured |

Recommended order: **1 → 2 → 3**. Guide 2 sets `AGV_ID` inside the service unit, so if you
only ever run via systemd you can skip the system-wide step in guide 1 — but doing both is
harmless and keeps manual `python3 main.py` runs working too.
