# Ananta CLI Commands (User Path)

Diese Seite zeigt den **normalen Nutzerpfad** ueber `ananta ...`.

## Schnellstartbefehle

```bash
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
ananta first-run
ananta status
ananta ask "Was sollte ich als naechstes pruefen?"
ananta plan "Bereite den Release-Abschluss vor"
ananta analyze "Analysiere dieses Repository"
ananta review "Pruefe die Login-Aenderungen"
ananta diagnose "Frontend erreicht den Hub nicht"
ananta patch "Plane einen kleinen Fix fuer die Validierung"
ananta repair-admin "Service restart loop nach Paketupdate"
ananta new-project "Baue ein kleines Release-Check-Tool fuer Maintainer"
ananta evolve-project "Erweitere den Dashboard-Flow um einen Projektstartmodus"
ananta update --help
ananta tui --help
ananta doctor
ananta web
```

## Hinweise zu `tui` und `web`

- `ananta tui` delegiert auf die vorhandene TUI-Laufzeit.
- `ananta web` gibt die Web-URL aus (Default `http://localhost:4200`, optional ueber `ANANTA_WEB_URL` oder `--url`).
- `ananta update` aktualisiert eine bestehende Installation sicher und zeigt Rollback-Hinweise an.

## Weitere Setup-Dokumente

- `docs/setup/bootstrap-install.md`
- `docs/setup/ananta_update.md`
- `docs/setup/quickstart.md`
- `docs/setup/ananta_init.md`
- `docs/golden-path-cli.md`

## Dependency precondition decision

- Entscheidung: kein Code-Refactor fuer "help ohne Backend-Abhaengigkeiten" in diesem Track.
- Begruendung: es gibt aktuell keinen nachgewiesenen Nutzerwert, der den Eingriff in CLI-Importpfade rechtfertigt.
- Dokumentierter Zustand: der normale Nutzerpfad bleibt `ananta ...`; Entwickler-Fallback bleibt `python -m agent.cli_goals ...` laut `docs/cli/developer_entrypoints.md`.
