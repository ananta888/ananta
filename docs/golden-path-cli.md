# Offizieller CLI-Golden-Path

Der CLI-Golden-Path ist der bevorzugte Standardarbeitsweg fuer lokale Nutzer und Reviewer, die schnell einen Goal->Plan->Tasks Start aus der Konsole brauchen.

Ziel: **ein eindeutiger Standardweg**, der sich vom Diagnose- oder Expertenpfad klar abgrenzt.

## Golden Path: Goal planen (Standard)

1. Offiziellen Ersteinstieg anzeigen:
   - `python -m agent.cli_goals --first-run`
2. Readiness pruefen:
   - `python -m agent.cli_goals --status`
3. Ein Goal als Shortcut planen:
   - `python -m agent.cli_goals plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"`
4. Erfolgssignal:
   - CLI gibt Goal-ID, Status, Task-Anzahl und das naechste Detailkommando aus.
5. Danach:
   - `python -m agent.cli_goals --tasks --task-status todo`
   - `python -m agent.cli_goals --goal-detail <goal_id>`

## Erste Hilfe bei typischen Startfehlern

- Login-Fehler: `ANANTA_USER` und `ANANTA_PASSWORD` pruefen.
- Verbindungsfehler: Hub starten oder `ANANTA_BASE_URL` auf den Hub setzen.
- Governance-/Policy-Blockierung: Goal enger formulieren oder Governance-Modus pruefen.

## Offizielle Shortcuts

- `ask`: Frage beantworten + naechste pruefbare Schritte
- `plan`: planbare Aufgabenstruktur
- `analyze`: Repo-/Systemanalyse
- `review`: Aenderungen bewerten
- `diagnose`: Start-/Compose-/Health-Diagnose
- `repair-admin`: Admin-Repair Shared Foundation (bounded diagnosis + dry-run-first bounded repair)
- `patch`: kleinen Patch planen
- `new-project`: neues Softwareprojekt aus einer Idee anlegen
- `evolve-project`: bestehendes Projekt kontrolliert weiterentwickeln

## Produktpfade per CLI

Neues Softwareprojekt anlegen:

```bash
python -m agent.cli_goals new-project "Baue ein kleines Release-Check-Tool fuer Maintainer"
```

Erwartetes Erfolgssignal: ein Goal mit Modus `new_software_project`, erstellten Tasks, Projekt-Blueprint, initialem Backlog und sichtbaren Review-/Governance-Hinweisen.

Existierendes Projekt weiterentwickeln:

```bash
python -m agent.cli_goals evolve-project "Erweitere den Dashboard-Flow um einen Projektstartmodus"
```

Erwartetes Erfolgssignal: ein Goal mit Modus `project_evolution`, kleinen Aenderungsschritten, Risiko-/Testsicht und Review-Plan.

## Nebenpfade (nicht Golden Path)

- Status/Readiness: `python -m agent.cli_goals --status`
- Task-Liste: `python -m agent.cli_goals --tasks --task-status todo`
- Diagnose-Fokus: `python -m agent.cli_goals diagnose "..."`
- Admin-Repair Shared Foundation: `python -m agent.cli_goals repair-admin "Service restart loop nach Paketupdate"`

Erwartetes Repair-Output-Signal:
- sichtbare Abschnitte fuer `Diagnosis`, `Repair Plan`, `Risk and Approval`, `Verification`
- step-confirmed Ausfuehrungsmodell mit bounded Aktionen
- audit-ready Session-Trail und hook-ready Bridge-IDs fuer spaetere KRITIS-Haertung

Der CLI-Pfad ist abgeschlossen, wenn `--first-run` den Einstieg erklaert, `--status` die Bereitschaft prueft und ein Shortcut-Goal Goal-ID, Status, Task-Anzahl und naechstes Detailkommando ausgibt.
