# Offizieller CLI-Golden-Path

Der CLI-Golden-Path ist der bevorzugte Standardarbeitsweg fuer lokale Nutzer und Reviewer, die schnell einen Goal->Plan->Tasks Start aus der Konsole brauchen.

Ziel: **ein eindeutiger Standardweg**, der sich vom Diagnose- oder Expertenpfad klar abgrenzt.

## Golden Path: Goal planen (Standard)

1. Base URL setzen (optional):
   - `ANANTA_BASE_URL=http://localhost:5000`
2. Ein Goal als Shortcut planen:
   - `python -m agent.cli_goals plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"`
3. Erfolgssignal:
   - CLI gibt Goal-ID und erstellte Task-IDs aus.

## Offizielle Shortcuts

- `ask`: Frage beantworten + naechste pruefbare Schritte
- `plan`: planbare Aufgabenstruktur
- `analyze`: Repo-/Systemanalyse
- `review`: Aenderungen bewerten
- `diagnose`: Start-/Compose-/Health-Diagnose
- `patch`: kleinen Patch planen

## Nebenpfade (nicht Golden Path)

- Status/Readiness: `python -m agent.cli_goals status`
- Task-Liste: `python -m agent.cli_goals tasks --status todo`
- Diagnose-Fokus: `python -m agent.cli_goals diagnose "..."`

