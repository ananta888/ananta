#!/usr/bin/env python3
"""T07.04: Lint-Skript für snake_tutor_texts.yaml.

Prüft:
  - Alle Events haben overview/deep/expert Texte
  - Alle 9 TUI-Sections haben Willkommenstexte in allen Tiefen
  - Kein overview-Text ist länger als 60 Zeichen pro Satz
  - Kein deep-Text ist länger als 200 Zeichen
  - Kein expert-Text ist länger als 400 Zeichen
  - Alle idle-Texte vorhanden

Aufruf: python scripts/lint_tutor_texts.py
Exit 0 = alles OK, Exit 1 = Fehler gefunden.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
YAML_PATH = ROOT / "client_surfaces" / "operator_tui" / "snake_tutor_texts.yaml"

REQUIRED_EVENTS = [
    "food_eaten", "collision_wall", "collision_self",
    "level_up_5", "level_up_10", "level_up_20",
    "zone_header", "zone_nav", "zone_content", "zone_detail",
]
REQUIRED_SECTIONS = [
    "dashboard", "goals", "tasks", "artifacts", "knowledge",
    "config", "system", "audit", "terminal",
]
DEPTHS = ("overview", "deep", "expert")
MAX_LEN = {"overview": 60, "deep": 200, "expert": 400}

errors: list[str] = []


def check_text(path: str, depth: str, text: str) -> None:
    if not text or not text.strip():
        errors.append(f"LEER  {path}.{depth}")
        return
    max_chars = MAX_LEN[depth]
    if depth == "overview":
        for sentence in text.replace("\n", " ").split("."):
            sentence = sentence.strip()
            if sentence and len(sentence) > max_chars:
                errors.append(f"LANG  {path}.{depth}: '{sentence[:40]}...' ({len(sentence)} > {max_chars})")
    else:
        if len(text.strip()) > max_chars:
            errors.append(f"LANG  {path}.{depth}: {len(text.strip())} > {max_chars} Zeichen")


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML nicht installiert. pip install pyyaml", file=sys.stderr)
        return 1

    if not YAML_PATH.exists():
        print(f"ERROR: {YAML_PATH} nicht gefunden", file=sys.stderr)
        return 1

    with YAML_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        print("ERROR: YAML hat unerwartetes Format (kein dict)", file=sys.stderr)
        return 1

    # -- events --
    events = data.get("events") or {}
    for event_key in REQUIRED_EVENTS:
        if event_key not in events:
            errors.append(f"FEHLT events.{event_key}")
            continue
        entry = events[event_key]
        if not isinstance(entry, dict):
            errors.append(f"FEHLT events.{event_key} (kein dict)")
            continue
        for depth in DEPTHS:
            if depth not in entry:
                errors.append(f"FEHLT events.{event_key}.{depth}")
            else:
                check_text(f"events.{event_key}", depth, str(entry[depth]))

    # -- sections --
    sections = data.get("sections") or {}
    for section_id in REQUIRED_SECTIONS:
        if section_id not in sections:
            errors.append(f"FEHLT sections.{section_id}")
            continue
        entry = sections[section_id]
        if not isinstance(entry, dict):
            errors.append(f"FEHLT sections.{section_id} (kein dict)")
            continue
        for depth in DEPTHS:
            if depth not in entry:
                errors.append(f"FEHLT sections.{section_id}.{depth}")
            else:
                check_text(f"sections.{section_id}", depth, str(entry[depth]))

    # -- idle --
    idle = data.get("idle")
    if not idle:
        errors.append("FEHLT idle (Abschnitt komplett)")
    elif isinstance(idle, list):
        if len(idle) == 0:
            errors.append("FEHLT idle: Liste ist leer")
    elif isinstance(idle, dict):
        for depth in DEPTHS:
            if depth not in idle:
                errors.append(f"FEHLT idle.{depth}")

    if errors:
        print(f"snake_tutor_texts.yaml – {len(errors)} Fehler gefunden:\n")
        for e in errors:
            print(f"  {e}")
        return 1

    print(f"OK  snake_tutor_texts.yaml – keine Fehler ({len(REQUIRED_EVENTS)} Events, {len(REQUIRED_SECTIONS)} Sections geprüft)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
