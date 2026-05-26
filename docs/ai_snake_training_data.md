# AI-Snake Training Data

## Speicherorte

Alle Trainingsdaten liegen local-first unter:

- `~/.config/ananta/ai_snake/prediction_profile.active.json`
- `~/.config/ananta/ai_snake/prediction_events.jsonl`
- `~/.config/ananta/ai_snake/learned_patterns.json`
- `~/.config/ananta/ai_snake/exports/`
- `~/.config/ananta/ai_snake/training_import_audit.log`

## Relevante Kommandos

- `:ai data path`
- `:ai data show`
- `:ai patterns`
- `:ai pattern <id>`
- `:ai pattern explain <id>`
- `:ai pattern enable <id>`
- `:ai pattern disable <id>`
- `:ai pattern delete <id>`
- `:ai data export --stdout --format json [--include-events]`
- `:ai data export <path> --format json [--include-events]`
- `:ai data export-md <path> [--json-ref <bundle.json>]`
- `:ai data import <path> --preview`
- `:ai data import <path> [--disabled] [--conflict keep_higher_confidence|overwrite|keep_local|merge_counters|import_disabled_copy] [--ignore-checksum]`
- `:ai data compact`
- `:ai data delete events`
- `:ai data delete patterns`
- `:ai data reset`
- `:ai learning on|off|pause|status`

## Privacy-Klassen

- `public_ui`: UI-nahe, unkritische Trainingssignale
- `workspace`: lokale Arbeitskontext-Signale
- `private_local`: lokal erlaubte, aber nicht roh exportierbare Inhalte
- `sensitive_blocked`: darf standardmäßig nicht in Bundles exportiert werden

Export erzeugt `privacy_manifest`. Bei `private_local` Inhalten erscheint eine Warnung im Status.

## Manueller Korrektur-Workflow (Export -> Edit -> Import)

1. Exportiere Bundle:
   - `:ai data export /tmp/ai-snake-bundle.json --format json`
2. Bearbeite JSON manuell (Patterns/Felder korrigieren).
3. Validiere Datei:
   - `./.venv/bin/python scripts/ai_snake_training_data.py validate /tmp/ai-snake-bundle.json`
4. Vorschau ohne Änderung:
   - `:ai data import /tmp/ai-snake-bundle.json --preview`
5. Übernehmen:
   - `:ai data import /tmp/ai-snake-bundle.json`

Import validiert das Bundle vollständig. Bei Fehlern wird der JSON-Pfad im Fehlertext ausgegeben.
Importierte manuell geänderte Patterns erhalten `extensions.edited_by_user=true`.

Pflichtfelder für manuell bearbeitete Patterns bleiben:

- `human_explanation`
- `ai_hint`
- `confidence`
- `predicted_intent`

## JSON- und Markdown-Beispiele

- JSON-Bundle: `:ai data export /tmp/ai-snake-bundle.json --format json`
- Markdown-Report: `:ai data export-md /tmp/ai-snake-report.md --json-ref /tmp/ai-snake-bundle.json`

## Recovery bei kaputtem Profil/Store

1. Integrität prüfen:
   - `./.venv/bin/python scripts/ai_snake_training_data.py validate ~/.config/ananta/ai_snake/prediction_profile.active.json`
2. Falls defekt:
   - Profil sichern (manuell kopieren) und `:ai data reset` ausführen.
3. Falls vorhanden:
   - `.bak` Dateien aus dem Verzeichnis wiederherstellen.
4. Optional:
   - importiere letztes valides Bundle erneut mit `--preview` vor Übernahme.

## Local-first Invariante

Trainingsdaten werden nicht automatisch an Hub/GitHub/Cloud-LLM übertragen.
Export/Import sind explizite User-Kommandos.
