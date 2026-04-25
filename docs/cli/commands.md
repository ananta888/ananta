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
ananta tui --help
ananta doctor
ananta web
```

## Hinweise zu `tui` und `web`

- `ananta tui` delegiert auf die vorhandene TUI-Laufzeit.
- `ananta web` gibt die Web-URL aus (Default `http://localhost:4200`, optional ueber `ANANTA_WEB_URL` oder `--url`).

## Weitere Setup-Dokumente

- `docs/setup/quickstart.md`
- `docs/setup/ananta_init.md`
- `docs/golden-path-cli.md`
