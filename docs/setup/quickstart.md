# Quickstart (CLI)

Dieser Quickstart nutzt den einheitlichen Nutzerpfad ueber `ananta ...`.

## 0) Optional: Bootstrap installer (recommended for first install)

If `ananta` is not installed yet, use the one-file bootstrap installer first:

```bash
curl -fsSL https://raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.sh -o install-ananta.sh
bash install-ananta.sh
```

Windows 11 PowerShell:

```powershell
iwr https://raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.ps1 -OutFile install-ananta.ps1; .\install-ananta.ps1
```

Details and safer inspect-run variants: `docs/setup/bootstrap-install.md`

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

- `docs/setup/bootstrap-install.md`
- `docs/setup/ananta_init.md`
- `docs/setup/ananta_update.md`
- `docs/setup/deployment_targets.md`
- `docs/cli/commands.md`
