# Quickstart (CLI)

Dieser Quickstart nutzt den einheitlichen Nutzerpfad ueber `ananta ...`.

## 1) Runtime-Profil erzeugen

```bash
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
```

`local-dev` ist der Standard fuer lokale Nutzung ohne Docker-Zwang.

## 2) Readiness pruefen

```bash
ananta first-run
ananta status
ananta doctor
```

## 3) Erstes Goal starten

```bash
ananta ask "Was sollte ich als naechstes pruefen?"
ananta plan "Bereite den Release-Abschluss vor"
ananta analyze "Analysiere dieses Repository"
```

## 4) Optional: Produkt-Shortcuts

```bash
ananta new-project "Baue ein kleines Release-Check-Tool fuer Maintainer"
ananta evolve-project "Erweitere den Dashboard-Flow um einen Projektstartmodus"
```

## 5) Optional: Runtime-Oberflaechen

```bash
ananta tui --help
ananta web
```

## Optional: Docker/Podman Deployment-Profile

Falls du statt `local-dev` eine containernahe Auspraegung vorbereiten willst:

```bash
ananta init --yes --runtime-mode sandbox --llm-backend ollama --deployment-target docker-compose
ananta init --yes --runtime-mode sandbox --llm-backend ollama --deployment-target podman
```

Weiterfuehrung:

- `docs/setup/ananta_init.md`
- `docs/setup/deployment_targets.md`
- `docs/cli/commands.md`
