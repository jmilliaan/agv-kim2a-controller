# 2. Run the controller as a `systemd` service

This makes the controller **start automatically on boot** and **restart if it crashes**,
and gives you one place for logs (`journalctl`). The controller already handles `SIGTERM`
gracefully — on stop it commands the drives to a safe stop (0 rpm + CiA-402 Quick stop) and
zeros the DO coils — so a normal `systemctl stop` leaves the AGV safe.

---

## Step 1 — give the user access to the hardware

The wheel drives + magnetic sensor talk over a USB-CAN adapter (slcan, e.g.
`/dev/ttyACM0`), which is a serial device. The service user needs the `dialout` group:

```bash
sudo usermod -aG dialout gvipc-06
```

Log out/in (or reboot) for the group change to take effect. Modbus TCP (DIO module) and the
RFID reader are plain TCP — no special permissions needed.

> If your CAN adapter enumerates as something other than `/dev/ttyACM0`, set the correct path
> in `profiles/agv-evo-01.json` → `motor_can.CHANNEL` (or set it to `null` to auto-detect the
> CANable2 by USB VID/PID).

---

## Step 2 — create the service unit

```bash
sudo nano /etc/systemd/system/agv-controller.service
```

Paste:

```ini
[Unit]
Description=AGV EVO controller (KIM2A)
# Wait for the network so Modbus TCP / RFID connects come up cleanly.
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gvipc-06
Group=gvipc-06
WorkingDirectory=/home/gvipc-06/agv-kim2a-controller
Environment=AGV_ID=agv-evo-01
# Unbuffered so logs reach journald immediately.
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 main.py

# Restart on crash; back off so we don't hammer a missing device.
Restart=on-failure
RestartSec=3
# Graceful stop: main.py traps SIGTERM and safes the drives. Give it time.
KillSignal=SIGTERM
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

> Using a virtualenv? Replace `ExecStart` with the venv interpreter, e.g.
> `ExecStart=/home/gvipc-06/agv-kim2a-controller/.venv/bin/python main.py`.

---

## Step 3 — enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable agv-controller.service     # start on every boot
sudo systemctl start  agv-controller.service     # start now
```

Check status and follow logs:

```bash
systemctl status agv-controller.service
journalctl -u agv-controller.service -f          # live logs (Ctrl+C to exit)
```

You should see the driver banner (`DIO … ENABLED`, `MOTOR CAN … ENABLED`, etc.) and
`CANMotorDriver online`. The dashboard is now at `http://localhost:5000`.

---

## Everyday commands

```bash
sudo systemctl restart agv-controller     # restart after editing a profile
sudo systemctl stop    agv-controller     # stop (safes the drives first)
sudo systemctl disable agv-controller     # stop auto-start on boot
journalctl -u agv-controller -e           # jump to the end of the log
journalctl -u agv-controller --since "1 hour ago"
```

The app also keeps its own rotating logs in
`/home/gvipc-06/agv-kim2a-controller/logs/` ([logger.py](../logger.py)).

---

## Dashboard restart button + module toggles

The Parameters page (`/params`) has a MODULES panel (DIGITAL I/O, CAN, SAFETY WATCHDOG,
HORN) that writes straight to the active profile's `features` block, and a RESTART
SERVICE button. These are boot-time flags — drivers/tasks are only created at startup —
so a toggle only takes effect after the next restart, which the button gives you in one
click.

Since the dashboard runs as `gvipc-06` (not root), that one `systemctl` command needs
passwordless sudo:

```bash
sudo visudo -f /etc/sudoers.d/agv-controller
```

Paste exactly:

```
gvipc-06 ALL=(root) NOPASSWD: /usr/bin/systemctl restart agv-controller.service
```

Without this entry, the RESTART SERVICE button fails (check `journalctl -u
agv-controller`) and you'll need to restart manually via "Everyday commands" above.

---

## Bench / no-hardware runs

If you're running on a PC with nothing wired up and don't want connect retries in the log,
disable the hardware subsystems in `profiles/agv-evo-01.json` → `features`
(`DIO_ENABLED`, `MOTOR_CAN_ENABLED`, `CAN_ENABLED`, `RFID_ENABLED` → `0`). The service still
starts cleanly and the dashboard still comes up — see [config.py](../config.py).
`SAFETY_ENABLED` / `HORN_ENABLED` work the same way if you want to bench-test without
the safety watchdog or horn outputs.
