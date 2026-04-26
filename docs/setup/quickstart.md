# Quickstart (CLI)

Dieser Quickstart nutzt den einheitlichen Nutzerpfad ueber `ananta ...`.

Wenn du statt CLI-only den kompletten Stack mit Web-UI brauchst, nutze den Full-Stack-Pfad in `README.md` und `docs/INSTALL_TEST_BETRIEB.md`.

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

## 2) Startpfad waehlen

### A) Nur CLI konfigurieren

Dieser Pfad erzeugt nur das lokale Profil. Er startet keinen Hub und keine Worker.

```bash
ananta first-run
```

Nutze diesen Pfad, wenn ein Hub bereits laeuft oder du `ANANTA_BASE_URL` auf einen vorhandenen Hub setzt.

### B) Lokalen Hub direkt ohne Docker starten

Terminal 1:

```bash
export ROLE=hub
export PORT=5000
export HUB_URL=http://localhost:5000
export HUB_CAN_BE_WORKER=true
export INITIAL_ADMIN_USER=admin
export INITIAL_ADMIN_PASSWORD=ananta-local-dev-admin
python -m agent.ai_agent
```

Damit startet der Hub lokal auf Port `5000`. Mit `HUB_CAN_BE_WORKER=true` kann der Hub fuer den ersten lokalen Quickstart auch einfache Worker-Aufgaben selbst uebernehmen.

Terminal 2:

```bash
export ANANTA_BASE_URL=http://localhost:5000
export ANANTA_USER=admin
export ANANTA_PASSWORD=ananta-local-dev-admin
ananta status
ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"
```

### C) Optional separaten lokalen Worker starten

Wenn du Hub und Worker getrennt testen willst, starte zusaetzlich einen zweiten Agent-Prozess.

Terminal 3:

```bash
export ROLE=worker
export AGENT_NAME=local-worker
export PORT=5001
export HUB_URL=http://localhost:5000
export AGENT_URL=http://localhost:5001
python -m agent.ai_agent
```

Der Worker registriert sich beim Hub. Der Hub bleibt Owner von Goals, Tasks, Policy, Approval und Audit.

## 3) Readiness pruefen

```bash
ananta first-run
ananta status
ananta doctor
```

## 4) Erstes Goal starten

```bash
ananta ask "Was sollte ich als naechstes pruefen?"
ananta plan "Bereite den Release-Abschluss vor"
ananta analyze "Analysiere dieses Repository"
```

## 5) Optional: Produkt-Shortcuts

```bash
ananta new-project "Baue ein kleines Release-Check-Tool fuer Maintainer"
ananta evolve-project "Erweitere den Dashboard-Flow um einen Projektstartmodus"
```

## 6) Optional: Runtime-Oberflaechen

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
