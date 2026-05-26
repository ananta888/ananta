# AI-Snake Training Data

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

Importierte manuell geänderte Patterns erhalten in `extensions.edited_by_user=true` eine Markierung.

Pflichtfelder für manuell bearbeitete Patterns bleiben:

- `human_explanation`
- `ai_hint`
- `confidence`
- `predicted_intent`
