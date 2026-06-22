# 1. Make `AGV_ID` permanent

The controller picks its profile from the `AGV_ID` environment variable
([config.py](../config.py)): it loads `profiles/$AGV_ID.json`. If `AGV_ID` is unset it
defaults to `agv-evo-01`. Set it explicitly so the right profile is always used and the
second unit (`agv-evo-02`) can't accidentally load the wrong one.

> If you run the controller **only** through the systemd service (guide 2), the service file
> already sets `AGV_ID` via `Environment=` and you can skip this guide. Setting it
> system-wide as well is harmless and keeps manual `python3 main.py` runs correct.

Pick **one** of the methods below.

---

## Method A — system-wide via `/etc/environment` (recommended)

Affects every login shell and most services. Persists across reboots.

```bash
echo 'AGV_ID=agv-evo-01' | sudo tee -a /etc/environment
```

Log out and back in (or reboot), then verify:

```bash
echo "$AGV_ID"          # → agv-evo-01
```

To change the value later, edit the file and replace the line:

```bash
sudo nano /etc/environment
```

> Note: `/etc/environment` is **not** a script — it only takes plain `KEY=value` lines
> (no `export`, no shell expansion).

---

## Method B — per-user via the shell profile

Affects only the `gvipc-06` user's interactive shells. Use this if you don't want it
system-wide.

```bash
echo 'export AGV_ID=agv-evo-01' >> ~/.profile
```

(`~/.bashrc` works too, but `~/.profile` is read by the graphical login session as well,
which matters for the kiosk in guide 3.)

Apply without logging out:

```bash
source ~/.profile
echo "$AGV_ID"          # → agv-evo-01
```

---

## Verify the controller sees it

```bash
cd /home/gvipc-06/agv-kim2a-controller
python3 -c "import config; print('profile:', config.AGV_ID)"
# → profile: agv-evo-01
```

If it prints `agv-evo-01` you're done. (On the second unit, use `agv-evo-02` everywhere
above and make sure `profiles/agv-evo-02.json` exists.)
