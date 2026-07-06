"""CTA-014: Zoom-Raum/Ablaufplan als schwache Context-Hints.

Uebersetzt zoom_room_id + timestamp anhand des classroom-Config-Blocks
(agent_cfg.classroom.room_mappings / .schedule) in gewichtete Hints
und retrieval_filters. Liefert nie Antworten — nur Eingrenzung.
"""
from __future__ import annotations

from datetime import datetime, timezone

HINT_WEAK = "weak"
HINT_MEDIUM = "medium"
HINT_STRONG = "strong"

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _classroom_cfg(cfg: dict | None) -> dict:
    block = (cfg or {}).get("classroom")
    return block if isinstance(block, dict) else {}


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _minutes(value: str) -> int | None:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def build_context_hints(*, zoom_room_id: str | None, timestamp: object, cfg: dict | None) -> dict:
    """Rueckgabe: {ranked_context_hints, retrieval_filters}.

    Ohne Mapping/Ablaufplan: leere Hints, kein Fehler — die Analyse
    laeuft dann ungefiltert weiter (Acceptance CTA-014).
    """
    classroom_cfg = _classroom_cfg(cfg)
    hints: list[dict] = []
    retrieval_filters: dict = {}

    room_mappings = classroom_cfg.get("room_mappings") if isinstance(classroom_cfg.get("room_mappings"), dict) else {}
    room_key = str(zoom_room_id or "").strip()
    room_entry = room_mappings.get(room_key) if room_key else None
    if isinstance(room_entry, dict):
        # Ein Raum-Hint allein ist nie strong (Acceptance).
        hints.append({
            "kind": "room",
            "value": {
                "group": room_entry.get("group"),
                "module_scope": room_entry.get("module_scope"),
            },
            "confidence": HINT_MEDIUM,
        })
        if room_entry.get("module_scope"):
            retrieval_filters["module_scope"] = str(room_entry["module_scope"])

    parsed_ts = _parse_timestamp(timestamp)
    schedule = classroom_cfg.get("schedule") if isinstance(classroom_cfg.get("schedule"), list) else []
    if parsed_ts is not None and schedule:
        weekday = _WEEKDAYS[parsed_ts.weekday()]
        # Slot-Vergleich in der Zeitzone des Ablaufplans (Default UTC),
        # damit der Randfall 08:59/09:00 deterministisch bleibt.
        minute_of_day = parsed_ts.hour * 60 + parsed_ts.minute
        for slot in schedule:
            if not isinstance(slot, dict):
                continue
            slot_day = str(slot.get("day") or "").strip().lower()[:3]
            if slot_day and slot_day != weekday:
                continue
            start = _minutes(str(slot.get("start") or ""))
            end = _minutes(str(slot.get("end") or ""))
            if start is None or end is None:
                continue
            if not (start <= minute_of_day < end):
                continue
            confidence = HINT_MEDIUM if (room_entry and slot.get("module_id") == (room_entry or {}).get("module_scope")) else HINT_WEAK
            hints.append({
                "kind": "schedule",
                "value": {
                    "schedule_slot": f"{slot_day or 'any'} {slot.get('start')}-{slot.get('end')}",
                    "module_id": slot.get("module_id"),
                    "task_id": slot.get("task_id"),
                },
                "confidence": confidence,
            })
            if slot.get("module_id") and "module_scope" not in retrieval_filters:
                retrieval_filters["module_scope"] = str(slot["module_id"])
            if slot.get("task_id"):
                retrieval_filters["task_scope"] = str(slot["task_id"])
            break

    return {"ranked_context_hints": hints, "retrieval_filters": retrieval_filters}
