# 3. Auto-display the dashboard on screen (XFCE kiosk)

Goal: after the PC boots and autologs into XFCE, the attached screen automatically opens the
AGV dashboard (`http://localhost:5000`) **full-screen**, with no panels, cursor idle, and the
screen never blanking. Assumes **autologin is already configured**.

This pairs with guide 2 (the controller running as a service). The kiosk just shows the web
UI the service serves.

---

## Step 1 — install a browser that has a kiosk mode

Chromium is the simplest:

```bash
sudo apt update
sudo apt install -y chromium-browser curl
```

> Ubuntu ships Chromium as a snap; the command is usually `chromium-browser` (sometimes
> `chromium`). Firefox works too — see the variant at the bottom.

---

## Step 2 — create a launch script

It waits for the controller to be serving, disables screen blanking, then opens Chromium in
kiosk mode.

```bash
nano /home/gvipc-06/agv-kim2a-controller/_setup_guide/agv-kiosk.sh
```

Paste:

```bash
#!/usr/bin/env bash
# Launch the AGV dashboard full-screen once the controller is up.
set -u

URL="http://localhost:5000"

# Stop XFCE/X from blanking or powering down the screen.
xset s off
xset s noblank
xset -dpms

# Wait until the Flask dashboard answers (controller may still be starting).
for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "$URL"; then break; fi
    sleep 2
done

# Clear any "didn't shut down cleanly" restore prompt from a previous run.
PROFILE="$HOME/.config/agv-kiosk"
mkdir -p "$PROFILE"

exec chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --check-for-update-interval=31536000 \
    --user-data-dir="$PROFILE" \
    --app="$URL"
```

Make it executable:

```bash
chmod +x /home/gvipc-06/agv-kim2a-controller/_setup_guide/agv-kiosk.sh
```

Test it from a desktop terminal (a kiosk window should fill the screen):

```bash
/home/gvipc-06/agv-kim2a-controller/_setup_guide/agv-kiosk.sh
```

Exit the test with **Alt+F4**, or from another machine/TTY: `pkill chromium`.

---

## Step 3 — run it automatically at XFCE login

XFCE runs any `.desktop` file in `~/.config/autostart/` when the session starts.

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/agv-dashboard.desktop
```

Paste:

```ini
[Desktop Entry]
Type=Application
Name=AGV Dashboard Kiosk
Exec=/home/gvipc-06/agv-kim2a-controller/_setup_guide/agv-kiosk.sh
X-GNOME-Autostart-enabled=true
Terminal=false
```

Reboot to verify the full chain:

```bash
sudo reboot
```

After boot you should land in XFCE (autologin) and the dashboard should appear full-screen
within a few seconds of the controller coming up.

---

## Optional polish

- **Hide the mouse cursor when idle:**
  ```bash
  sudo apt install -y unclutter
  ```
  Add to the top of `agv-kiosk.sh` (before `exec chromium-browser`):
  ```bash
  unclutter -idle 3 &
  ```
- **Auto-recover if the browser is closed/crashes:** wrap the `exec chromium-browser …`
  in a `while true; do … ; sleep 2; done` loop (drop the `exec`).
- **Second monitor / rotation:** set it once in XFCE *Settings → Display*; the kiosk uses
  whatever the session geometry is.

---

## Firefox variant

If you prefer Firefox, install it and replace the `exec` line in `agv-kiosk.sh`:

```bash
sudo apt install -y firefox
```

```bash
exec firefox --kiosk "$URL"
```

(Firefox kiosk has fewer flags; the `xset` blanking lines above still apply.)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Blank/black screen, no window | Confirm the controller is up: `systemctl status agv-controller` and `curl -sf http://localhost:5000`. |
| Screen still blanks after a while | Make sure the `xset` lines ran (they're in the script); also check *Settings → Power Manager* → display sleep = Never. |
| "Restore pages?" bar on start | Already handled by the dedicated `--user-data-dir` profile + crash-bubble flags; if it persists, delete `~/.config/agv-kiosk` and reboot. |
| Wrong command name | Try `chromium` instead of `chromium-browser` (`which chromium chromium-browser`). |
