"""
core/mapping_store.py — RFID mapping persistence and compilation layer.

Manages the per-AGV override file that lets operators edit RFID→action rules
from the web UI without touching the hand-tuned profile JSON.

File layout
-----------
    profiles/{id}_sequences.json          live override (rules list)
    profiles/{id}_sequences.json.bak.1    most recent backup (up to .bak.5)
    profiles/{id}_sequences.audit.jsonl   append-only change log
"""

import json
import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "profiles")
_MAX_RULES   = 32
_MAX_BACKUPS = 5

_PRESET_TYPES = {
    "end_cycle", "start_cycle", "timed_pause",
    "pause_until_tag", "pulse_pusher", "slow_zone",
}

DURATION_MAX = 60.0
COOLDOWN_MIN = 1.0
COOLDOWN_MAX = 60.0
TAG_MIN = 0
TAG_MAX = 999


# ── File paths ────────────────────────────────────────────────────────────────

def _override_path(agv_id: str) -> str:
    return os.path.join(_DIR, f"{agv_id}_sequences.json")

def _audit_path(agv_id: str) -> str:
    return os.path.join(_DIR, f"{agv_id}_sequences.audit.jsonl")

def _backup_path(agv_id: str, slot: int) -> str:
    return os.path.join(_DIR, f"{agv_id}_sequences.json.bak.{slot}")


# ── Load / save ───────────────────────────────────────────────────────────────

def load_rules(agv_id: str):
    """Return list of rules from the override file, or None if file doesn't exist.

    None (missing file) is distinct from [] (present but empty — user cleared all rules).
    """
    path = _override_path(agv_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("rules", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[MAPPING] Failed to load override file %s: %s", path, e)
        return None


def save_rules(agv_id: str, rules: list, source: str = "ui") -> None:
    """Atomically write rules to the override file.

    Rotates existing file into .bak.1 (shifting .bak.1→.bak.2, up to .bak.5),
    then writes atomically via tmp-file + os.replace to survive power loss.
    Appends one entry to the audit log.
    """
    path = _override_path(agv_id)

    # Rotate backups: .bak.4 → .bak.5, …, current → .bak.1
    if os.path.exists(path):
        for slot in range(_MAX_BACKUPS, 0, -1):
            src = _backup_path(agv_id, slot - 1) if slot > 1 else path
            dst = _backup_path(agv_id, slot)
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError as e:
                    logger.warning("[MAPPING] Backup rotation slot %d failed: %s", slot, e)

    # Atomic write
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"version": 1, "rules": rules}, f, indent=2)
    os.replace(tmp, path)

    _append_audit(agv_id, source, rules)
    logger.info("[MAPPING] Saved %d rules (source=%s)", len(rules), source)


def _append_audit(agv_id: str, source: str, rules: list) -> None:
    entry = {
        "ts":           time.time(),
        "source":       source,
        "rule_count":   len(rules),
        "rules_summary": [
            {"id": r.get("id", "?"), "type": r.get("type"), "enabled": r.get("enabled", True)}
            for r in rules
        ],
    }
    path = _audit_path(agv_id)
    try:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.warning("[MAPPING] Could not append audit log: %s", e)


# ── Audit / backup access ─────────────────────────────────────────────────────

def read_audit_log(agv_id: str, limit: int = 50) -> list:
    """Return the last `limit` audit entries in reverse-chronological order."""
    path = _audit_path(agv_id)
    if not os.path.exists(path):
        return []
    entries = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return []
    return list(reversed(entries[-limit:]))


def list_backups(agv_id: str) -> list:
    """Return info about available backup slots [{slot, ts, rule_count}]."""
    result = []
    for slot in range(1, _MAX_BACKUPS + 1):
        p = _backup_path(agv_id, slot)
        if os.path.exists(p):
            try:
                mtime = os.path.getmtime(p)
                with open(p) as f:
                    data = json.load(f)
                rule_count = len(data.get("rules", []))
            except (OSError, json.JSONDecodeError):
                mtime = 0
                rule_count = 0
            result.append({"slot": slot, "ts": mtime, "rule_count": rule_count})
    return result


def restore_backup(agv_id: str, slot: int) -> tuple:
    """Restore from backup slot. Returns (rules, errors)."""
    p = _backup_path(agv_id, slot)
    if not os.path.exists(p):
        return None, [{"rule_id": None, "field": "slot",
                        "message": f"Backup slot {slot} not found"}]
    try:
        with open(p) as f:
            data = json.load(f)
        rules = data.get("rules", [])
        errors = validate(rules)
        if errors:
            return None, errors
        save_rules(agv_id, rules, source=f"restore_slot_{slot}")
        return rules, []
    except (OSError, json.JSONDecodeError) as e:
        return None, [{"rule_id": None, "field": None,
                        "message": f"Restore failed: {e}"}]


# ── Export / import ───────────────────────────────────────────────────────────

def export_bundle(agv_id: str) -> dict:
    """Return the current override data as an exportable dict."""
    rules = load_rules(agv_id) or []
    return {"version": 1, "agv_id": agv_id, "exported_ts": time.time(), "rules": rules}


def import_bundle(agv_id: str, data: dict) -> list:
    """Validate and save an imported bundle. Returns errors list (empty = success)."""
    if not isinstance(data, dict):
        return [{"rule_id": None, "field": None, "message": "Invalid bundle format"}]
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return [{"rule_id": None, "field": None, "message": "rules must be a list"}]

    # Assign fresh IDs to any rule that is missing one
    for r in rules:
        if not r.get("id"):
            r["id"] = str(uuid.uuid4())

    errors = validate(rules)
    if errors:
        return errors

    save_rules(agv_id, rules, source="import")
    return []


# ── Validation ────────────────────────────────────────────────────────────────

def validate(rules: list) -> list:
    """Validate a rules list. Returns [{rule_id, field, message}] errors."""
    errors = []

    if not isinstance(rules, list):
        return [{"rule_id": None, "field": None, "message": "rules must be a list"}]

    if len(rules) > _MAX_RULES:
        errors.append({"rule_id": None, "field": None,
                        "message": f"Too many rules (max {_MAX_RULES})"})
        return errors

    # tag_dec → (rule_id, preset_type) for conflict detection across enabled rules
    all_tags: dict = {}

    for r in rules:
        rid   = r.get("id", "?")
        rtype = r.get("type")

        if rtype not in _PRESET_TYPES:
            errors.append({"rule_id": rid, "field": "type",
                            "message": f"Unknown preset type '{rtype}'"})
            continue

        if not r.get("enabled", True):
            continue  # disabled rules skip all conflict checks

        cooldown = r.get("cooldown_s", 5.0)
        try:
            cooldown = float(cooldown)
        except (TypeError, ValueError):
            cooldown = 0.0
        if not (COOLDOWN_MIN <= cooldown <= COOLDOWN_MAX):
            errors.append({"rule_id": rid, "field": "cooldown_s",
                            "message": f"cooldown_s must be {COOLDOWN_MIN}–{COOLDOWN_MAX}"})

        def _check_tag(field, value):
            if not isinstance(value, int) or not (TAG_MIN <= value <= TAG_MAX):
                errors.append({"rule_id": rid, "field": field,
                                "message": f"Tag must be integer {TAG_MIN}–{TAG_MAX}"})
                return False
            return True

        def _register_tag(field, tag):
            """Register a tag; allow end_cycle+start_cycle to share the same tag."""
            if tag in all_tags:
                existing_rid, existing_type = all_tags[tag]
                shared_ok = (
                    rtype in ("end_cycle", "start_cycle") and
                    existing_type in ("end_cycle", "start_cycle") and
                    rtype != existing_type
                )
                if not shared_ok:
                    errors.append({"rule_id": rid, "field": field,
                                    "message": f"Tag {tag} already used by rule {existing_rid}"})
            else:
                all_tags[tag] = (rid, rtype)

        def _check_duration(field, value):
            try:
                v = float(value)
            except (TypeError, ValueError):
                errors.append({"rule_id": rid, "field": field,
                                "message": f"{field} must be a number"})
                return False
            if not (0 < v <= DURATION_MAX):
                errors.append({"rule_id": rid, "field": field,
                                "message": f"{field} must be 0–{DURATION_MAX}s"})
                return False
            return True

        if rtype == "end_cycle":
            tag = r.get("tag")
            if _check_tag("tag", tag):
                _register_tag("tag", tag)

        elif rtype == "start_cycle":
            tag = r.get("tag")
            if _check_tag("tag", tag):
                _register_tag("tag", tag)
            _check_duration("pusher_duration_s", r.get("pusher_duration_s", 5.0))

        elif rtype == "timed_pause":
            tag = r.get("tag")
            if _check_tag("tag", tag):
                _register_tag("tag", tag)
            _check_duration("duration_s", r.get("duration_s"))

        elif rtype == "pause_until_tag":
            start  = r.get("start_tag")
            resume = r.get("resume_tag")
            ok_s = _check_tag("start_tag",  start)
            ok_r = _check_tag("resume_tag", resume)
            if ok_s and ok_r:
                if start == resume:
                    errors.append({"rule_id": rid, "field": "resume_tag",
                                    "message": "start_tag and resume_tag must differ"})
                else:
                    _register_tag("start_tag",  start)
                    _register_tag("resume_tag", resume)

        elif rtype == "pulse_pusher":
            tag = r.get("tag")
            if _check_tag("tag", tag):
                _register_tag("tag", tag)
            if r.get("direction") not in ("extend", "retract"):
                errors.append({"rule_id": rid, "field": "direction",
                                "message": "direction must be 'extend' or 'retract'"})
            _check_duration("duration_s", r.get("duration_s"))

        elif rtype == "slow_zone":
            start = r.get("start_tag")
            end   = r.get("end_tag")
            ok_s = _check_tag("start_tag", start)
            ok_e = _check_tag("end_tag",   end)
            if ok_s and ok_e:
                if start == end:
                    errors.append({"rule_id": rid, "field": "end_tag",
                                    "message": "start_tag and end_tag must differ"})
                else:
                    _register_tag("start_tag", start)
                    _register_tag("end_tag",   end)

    return errors


# ── Compilation: rules → engine sequence dicts ────────────────────────────────

def compile_to_sequences(rules: list) -> list:
    """Convert enabled preset rules to sequence dicts for the SequenceEngine."""
    seqs = []
    for r in rules:
        if not r.get("enabled", True):
            continue
        rtype = r["type"]
        rid8  = r["id"][:8]

        # Default cooldowns: stricter ops get 10s, speed/pause get 5s
        default_cd = 10.0 if rtype in ("end_cycle", "start_cycle", "pulse_pusher") else 5.0
        cooldown   = float(r.get("cooldown_s", default_cd))

        if rtype == "end_cycle":
            seqs.append({
                "name":             f"ui:{rid8}:end_cycle",
                "trigger":          {"type": "rfid", "rfid_tag": f"{r['tag']:04X}"},
                "requires_mode":    "running",
                "requires_at_home": bool(r.get("requires_at_home", False)),
                "cooldown_s":       cooldown,
                "actions": [
                    {"type": "stop_agv"},
                    {"type": "pusher_retract", "duration": 5.0},
                    {"type": "set_at_home", "value": True},
                    {"type": "end_cycle"},
                ],
            })

        elif rtype == "start_cycle":
            dur = float(r.get("pusher_duration_s", 5.0))
            seqs.append({
                "name":             f"ui:{rid8}:start_cycle",
                "trigger":          {"type": "rfid", "rfid_tag": f"{r['tag']:04X}"},
                "requires_mode":    "running",
                "requires_at_home": True,
                "cooldown_s":       cooldown,
                "actions": [
                    {"type": "stop_agv"},
                    {"type": "pusher_extend", "duration": dur},
                    {"type": "set_at_home", "value": False},
                    {"type": "set_speed", "speed": "HIGH"},
                    {"type": "resume"},
                ],
            })

        elif rtype == "timed_pause":
            seqs.append({
                "name":          f"ui:{rid8}:timed_pause",
                "trigger":       {"type": "rfid", "rfid_tag": f"{r['tag']:04X}"},
                "requires_mode": "running",
                "cooldown_s":    cooldown,
                "actions": [
                    {"type": "sequence_stop", "duration": float(r["duration_s"])},
                ],
            })

        elif rtype == "pause_until_tag":
            seqs.append({
                "name":          f"ui:{rid8}:pause_start",
                "trigger":       {"type": "rfid", "rfid_tag": f"{r['start_tag']:04X}"},
                "requires_mode": "running",
                "cooldown_s":    cooldown,
                "actions": [{"type": "stop_agv"}],
            })
            seqs.append({
                "name":          f"ui:{rid8}:pause_resume",
                "trigger":       {"type": "rfid", "rfid_tag": f"{r['resume_tag']:04X}"},
                "requires_mode": "running",
                "cooldown_s":    1.0,
                "actions": [{"type": "resume"}],
            })

        elif rtype == "pulse_pusher":
            action_type = "pusher_extend" if r["direction"] == "extend" else "pusher_retract"
            seqs.append({
                "name":          f"ui:{rid8}:pulse_pusher",
                "trigger":       {"type": "rfid", "rfid_tag": f"{r['tag']:04X}"},
                "requires_mode": "running",
                "cooldown_s":    cooldown,
                "actions": [
                    {"type": "stop_agv"},
                    {"type": action_type, "duration": float(r["duration_s"])},
                    {"type": "resume"},
                ],
            })

        elif rtype == "slow_zone":
            seqs.append({
                "name":          f"ui:{rid8}:slow_start",
                "trigger":       {"type": "rfid", "rfid_tag": f"{r['start_tag']:04X}"},
                "requires_mode": "running",
                "cooldown_s":    cooldown,
                "actions": [{"type": "set_speed", "speed": "SLOW"}],
            })
            seqs.append({
                "name":          f"ui:{rid8}:slow_end",
                "trigger":       {"type": "rfid", "rfid_tag": f"{r['end_tag']:04X}"},
                "requires_mode": "running",
                "cooldown_s":    cooldown,
                "actions": [{"type": "set_speed", "speed": "HIGH"}],
            })

    return seqs


# ── Decompile: profile sequences → preset rules (best-effort) ─────────────────

def decompile_profile_sequences(profile_seqs: list) -> tuple:
    """Reverse-compile profile sequences into preset rules where possible.

    Returns (rules, raw_seqs) — raw_seqs are sequences that didn't match any
    preset pattern and will be shown as read-only in the UI.
    """
    provisional = []   # intermediate rules with types prefixed "_"
    raw_seqs    = []

    def _tag_dec(hex_str):
        try:
            return int(hex_str, 16)
        except (ValueError, TypeError):
            return None

    for seq in profile_seqs:
        actions  = seq.get("actions", [])
        trigger  = seq.get("trigger", {})
        tag_hex  = trigger.get("rfid_tag")
        tag_dec  = _tag_dec(tag_hex)
        cooldown = float(seq.get("cooldown_s", 5.0))
        at_home  = seq.get("requires_at_home")
        atypes   = [a["type"] for a in actions]

        if trigger.get("type") != "rfid" or tag_dec is None:
            raw_seqs.append(seq)
            continue

        # end_cycle
        if atypes == ["stop_agv", "pusher_retract", "set_at_home", "end_cycle"]:
            provisional.append({
                "id": str(uuid.uuid4()), "type": "end_cycle", "enabled": True,
                "tag": tag_dec,
                "requires_at_home": bool(at_home) if at_home is not None else False,
                "cooldown_s": cooldown,
            })
            continue

        # start_cycle
        if atypes == ["stop_agv", "pusher_extend", "set_at_home", "set_speed", "resume"]:
            ext_act = next((a for a in actions if a["type"] == "pusher_extend"), {})
            provisional.append({
                "id": str(uuid.uuid4()), "type": "start_cycle", "enabled": True,
                "tag": tag_dec,
                "pusher_duration_s": ext_act.get("duration", 5.0),
                "cooldown_s": cooldown,
            })
            continue

        # timed_pause
        if atypes == ["sequence_stop"]:
            provisional.append({
                "id": str(uuid.uuid4()), "type": "timed_pause", "enabled": True,
                "tag": tag_dec, "duration_s": actions[0].get("duration", 3.0),
                "cooldown_s": cooldown,
            })
            continue

        # pulse_pusher
        if (len(atypes) == 3 and atypes[0] == "stop_agv" and atypes[2] == "resume"
                and atypes[1] in ("pusher_extend", "pusher_retract")):
            direction = "extend" if atypes[1] == "pusher_extend" else "retract"
            provisional.append({
                "id": str(uuid.uuid4()), "type": "pulse_pusher", "enabled": True,
                "tag": tag_dec, "direction": direction,
                "duration_s": actions[1].get("duration", 5.0),
                "cooldown_s": cooldown,
            })
            continue

        # slow_zone start (set_speed SLOW)
        if atypes == ["set_speed"] and actions[0].get("speed") == "SLOW":
            provisional.append({
                "id": str(uuid.uuid4()), "type": "_slow_start", "enabled": True,
                "start_tag": tag_dec, "cooldown_s": cooldown,
            })
            continue

        # slow_zone end (set_speed HIGH)
        if atypes == ["set_speed"] and actions[0].get("speed") == "HIGH":
            provisional.append({
                "id": str(uuid.uuid4()), "type": "_slow_end", "enabled": True,
                "end_tag": tag_dec, "cooldown_s": cooldown,
            })
            continue

        # pause_until_tag start (stop_agv alone)
        if atypes == ["stop_agv"]:
            provisional.append({
                "id": str(uuid.uuid4()), "type": "_pause_start", "enabled": True,
                "start_tag": tag_dec, "cooldown_s": cooldown,
            })
            continue

        # pause_until_tag end (resume alone)
        if atypes == ["resume"]:
            provisional.append({
                "id": str(uuid.uuid4()), "type": "_pause_end", "enabled": True,
                "resume_tag": tag_dec, "cooldown_s": cooldown,
            })
            continue

        raw_seqs.append(seq)

    # Pair _slow_start + _slow_end into slow_zone
    paired = set()
    starts = [r for r in provisional if r["type"] == "_slow_start"]
    ends   = [r for r in provisional if r["type"] == "_slow_end"]
    for ss in starts:
        for se in ends:
            if ss["id"] not in paired and se["id"] not in paired:
                provisional.append({
                    "id": str(uuid.uuid4()), "type": "slow_zone", "enabled": True,
                    "start_tag": ss["start_tag"], "end_tag": se["end_tag"],
                    "cooldown_s": ss["cooldown_s"],
                })
                paired.add(ss["id"])
                paired.add(se["id"])
                break

    # Pair _pause_start + _pause_end into pause_until_tag
    ps_list = [r for r in provisional if r["type"] == "_pause_start"]
    pe_list = [r for r in provisional if r["type"] == "_pause_end"]
    for ps in ps_list:
        for pe in pe_list:
            if ps["id"] not in paired and pe["id"] not in paired:
                provisional.append({
                    "id": str(uuid.uuid4()), "type": "pause_until_tag", "enabled": True,
                    "start_tag": ps["start_tag"], "resume_tag": pe["resume_tag"],
                    "cooldown_s": ps["cooldown_s"],
                })
                paired.add(ps["id"])
                paired.add(pe["id"])
                break

    # Finalise: drop provisional helpers; unmatched halves → raw
    final_rules = []
    for r in provisional:
        if r["type"].startswith("_"):
            if r["id"] not in paired:
                raw_seqs.append({"_decompile_note": f"unmatched {r['type']}", **r})
        else:
            if r["id"] not in paired:   # not already merged into a pair
                final_rules.append(r)

    return final_rules, raw_seqs
