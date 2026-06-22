# Ananta

[![Quality And Docs](https://github.com/ananta888/ananta/actions/workflows/quality-and-docs.yml/badge.svg)](https://github.com/ananta888/ananta/actions/workflows/quality-and-docs.yml)
[![Backend Isolated Flows](https://github.com/ananta888/ananta/actions/workflows/backend-isolated-flows.yml/badge.svg)](https://github.com/ananta888/ananta/actions/workflows/backend-isolated-flows.yml)

@Sponsored by www.ananta.de

**ANANTA** steht fuer **Autonomous Networked Agents Navigate Trusted Artifacts**.

Ananta ist eine offene, local-first Multi-Agenten-Plattform fuer sichere KI-gestuetzte Softwareentwicklung. Sie verbindet Hub-Worker-Orchestrierung, deterministischen Projektkontext, CodeCompass-Artefakte, rollenbasierte Ausfuehrung und Least-Privilege-Policies, damit KI-Agenten produktiv arbeiten koennen, ohne blind Zugriff auf Code, Secrets oder Infrastruktur zu bekommen.

Ananta ist eine kontrollierte Hub-Worker-Plattform fuer goal-basierte Agentenarbeit. Du beschreibst ein Ziel; der Hub plant, priorisiert und delegiert Aufgaben, Worker fuehren die Arbeit in getrennten Laufzeitkontexten aus, und Ergebnisse werden ueber Pruefung und Artefakte nachvollziehbar gemacht.

## Sicherheitsgrenze / Nicht-Ziel

Ananta reduziert Risiken durch Hub-Kontrolle, Least-Privilege, getrennte Worker-Kontexte, deterministische Handler, Policy-Gates, Audit und Artefaktpruefung. Ananta garantiert aber keine vollstaendige Absichtserkennung ueber beliebig zerlegte Aufgaben hinweg.

Wenn ein grosses Ziel in viele einzeln harmlose Teilaufgaben zerlegt wird, kann kein System zuverlaessig beweisen, dass daraus spaeter nicht doch ein gefaehrlicher, unerwuenschter oder policy-widriger Gesamtzweck entsteht. Ananta macht Ausfuehrung kontrollierbarer und nachvollziehbarer, ersetzt aber keine menschliche Verantwortung fuer Ziel, Kontext und Zusammenbau von Ergebnissen.

Als Metapher: Beim Manhattan-Projekt arbeiteten sehr viele Menschen an stark getrennten Teilaufgaben; nicht jede beteiligte Person musste das volle Gesamtziel, die spaetere Wirkung oder alle Zusammenhaenge kennen. Genau diese Art von Kompartimentierung zeigt die Grenze: Ein einzelner Arbeitsschritt kann harmlos wirken, waehrend der spaetere Zusammenbau auf Zielebene kritisch ist. Ananta kann solche Arbeitsschritte begrenzen und auditieren, aber nicht allgemein beweisen, dass beliebig kombinierte Teilergebnisse niemals einem gefaehrlichen Gesamtzweck dienen.

Diese Grenze ist bewusst Teil der Hauptdokumentation: Ananta soll keine Scheinsicherheit versprechen, die technisch nicht belastbar garantiert werden kann.

Der Kern ist bewusst nicht "ein Chatbot mit Tools", sondern ein steuerbares System fuer:

- Goal -> Plan -> Task -> Execution -> Verification -> Artifact
- Hub-kontrollierte Orchestrierung statt Worker-zu-Worker-Automation
- Docker-basierte Hub- und Worker-Laufzeiten
- reproduzierbare Releases, CI-Gates und Security-/Governance-Regeln
- optional: Hub-Direct-Execution und Custom-Tool-Promotion — einfache,
  deterministische Anfragen ohne Worker-LLM, wiederkehrende Loesungen
  als geprueft-promotete Tools. Standardmaessig deaktiviert
  (`hub_direct_execution.enabled=false`), kein Default-Autonomie-Modus;
  der Hub entscheidet und dispatcht, ausgefuehrt wird in der
  WorkerRuntime. Siehe [docs/architecture/hub-direct-execution.md](docs/architecture/hub-direct-execution.md)
  und [docs/security/custom-tool-promotion.md](docs/security/custom-tool-promotion.md)

| Einstieg | Fuer wen | Link |
| --- | --- | --- |
| Direkt ausprobieren | lokale Nutzer und Reviewer | [Schnellstart](#schnellstart-in-5-minuten) |
| Ein-Kommando-Installation | lokale Nutzer und Reviewer | [Bootstrap Install](docs/setup/bootstrap-install.md) |
| Wofuer Ananta offiziell steht | Produkt-/Projekt-Orientierung | [Kern-Use-Cases](docs/use-cases.md) |
| Blueprint/Template/Team einfach verstehen | Erstnutzer und Demos | [Blueprint Product Model](docs/blueprint-product-model.md) |
| Standard-Blueprints mit Beispielen | Erstnutzer und Demos | [Standard Blueprints](docs/standard-blueprints.md) |
| Strategy-Game als Architektur-Lernschicht | Demos und technische Reviewer | [Ananta Strategy Game](docs/ananta-game/README.md) |
| Offizieller UI-Standardweg | Erstnutzer und Demos | [UI Golden Path](docs/golden-path-ui.md) |
| Offizieller CLI-Standardweg | lokale Nutzer und Reviewer | [CLI Golden Path](docs/golden-path-cli.md) |
| Offizieller Release-Standardweg | Maintainer und Betreiber | [Release Golden Path](docs/release-golden-path.md) |
| Passendes Produktprofil waehlen | Demo, Trial, Team oder Security-Kontext | [Produktprofile](docs/product-profiles.md) |
| Architektur verstehen | technische Reviewer | [Architektur](#architektur) |
| Release bewerten | Maintainer und Betreiber | [Release und Governance](#release-und-governance) |
| API nutzen | Integratoren | [Einfache CLI- und API-Beispiele](#einfache-cli--und-api-beispiele) |

## Kern-Use-Cases (offiziell)

Ananta fokussiert sich bewusst auf eine kleine Menge reproduzierbarer Kernanwendungsfaelle, damit Einstieg, Demo, Benchmarks und Produktprofile auf derselben Basis stehen.

- Repository verstehen
- Bugfix planbar und testbar machen
- Start/Deploy diagnostizieren (Compose/Health/Logs)
- Change Review (Risiken, Tests, Governance)
- Gefuehrte Goal-Erstellung fuer Erstnutzer
- Neues Softwareprojekt anlegen
- Existierendes Softwareprojekt weiterentwickeln
- Research-gestuetzte Projektweiterentwicklung mit DeerFlow und Evolver

Details: `docs/use-cases.md`. Reproduzierbare Demo-Flows stehen in `docs/demo-flows.md`

**Task Engine:** Lese-Operationen (`list_files`, `git status`, `json_validate` …) werden deterministisch ohne LLM-Call ausgeführt. Architektur und Ablaufdiagramme: [`docs/task-engine-deterministic-hybrid-llm-policy.md`](docs/task-engine-deterministic-hybrid-llm-policy.md)., inklusive des offiziellen DeerFlow+Evolver-Standardpfads. Strukturierte Eingaben fuer die neuen Softwarepfade stehen in `docs/goal-input-schemas.md`. Fuer Shell-Guardrails siehe `docs/security/shell-command-policy.md` und die Migrationsnotiz `docs/release/shell-command-policy-migration.md`.

**CodeCompass-Handoff:** Wie CodeCompass Snippets, Line-Ranges und ganze Dateien priorisiert an den ananta-worker weitergibt: `docs/codecompass-relevant-snippet-handoff.md`.

**Generated Source Line Policy:** Optionaler Worker-Qualitaetsguardrail gegen neu erzeugte Source-Monolithen. Die Policy ist standardmaessig deaktiviert und kann unter `generated_source_line_policy` ausgerollt werden. Contract und Defaults: `docs/contracts/generated-source-line-policy.md` und `docs/development/source-line-limit-policy.md`.

## Schnellstart in 5 Minuten

### A) CLI-first ohne Docker (lokal)

Wenn du primar die CLI nutzen willst, brauchst du keinen Docker-Stack:

- Voraussetzung: Der Befehl `ananta` ist installiert. Falls nicht, zuerst `docs/setup/bootstrap-install.md` nutzen.

```bash
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
ananta first-run
# status/plan benoetigen einen laufenden Hub + passende ANANTA_* Zugangsdaten
ananta status
ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"
```

Ausfuehrungs-Backend fuer Worker/CLI (leicht umschaltbar):

```bash
# Standard: interne Ananta-Worker-Ausfuehrung (empfohlen)
python -m pip install shell-gpt
export SGPT_EXECUTION_BACKEND=ananta-worker

# Alternative: OpenCode
npm i -g opencode-ai
export SGPT_EXECUTION_BACKEND=opencode
```

Weitere CLI-Einstiege:

- `docs/setup/quickstart.md`
- `docs/cli/commands.md`
- `docs/golden-path-cli.md`

Wenn du statt CLI-only den Hub/Worker lokal ohne Docker, das Frontend lokal oder den kompletten Full-Stack mit Docker brauchst, nutze die folgenden Pfade.

### B) Lokalen Hub und Worker ohne Docker starten

Dieser Pfad startet sowohl den Hub als auch Worker ohne Docker.

Terminal 1: (Hub starten)

```bash
export ROLE=hub
export PORT=5000
export HUB_URL=http://localhost:5000
export HUB_CAN_BE_WORKER=true
export INITIAL_ADMIN_USER=admin
export INITIAL_ADMIN_PASSWORD=ananta-local-dev-admin
python -m agent.ai_agent
```

Terminal 2: (CLI nutzen)

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

### D) Lokales Angular-Frontend ohne Docker starten

Falls du das Frontend lokal ohne Docker starten willst:

```bash
cd frontend-angular
npm install
npm start
```

Das Frontend ist danach unter `http://localhost:4200` erreichbar.

API-Verbindung zum Hub:

- Im lokalen Browser-Modus nutzt das Frontend standardmaessig `http://localhost:5000` als Hub sowie `http://localhost:5001`/`5002` fuer Worker-Defaults.
- Wenn dein Hub auf einer anderen URL/Port laeuft, passe die Agent-URLs im Frontend (Agent Directory) entsprechend an.

### E) Full-Stack (Docker + UI)

1. Umgebung vorbereiten:
   ```powershell
   .\setup.ps1
   ```
   Das Script prueft Docker, Python und Node, legt eine `.env` an und installiert lokale Abhaengigkeiten.

2. Lite-Stack starten:
   ```bash
   docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
   ```

3. Im Browser oeffnen:
   - Frontend: `http://localhost:4200`
   - Hub API: `http://localhost:5000`

4. Einloggen:
   - Benutzer: `admin`
   - Passwort: Wert aus `INITIAL_ADMIN_PASSWORD` in `.env`

5. Erstes Ziel starten:
   - Im Erststart `Neues Projekt anlegen` waehlen oder im Arbeitsbereich `Planen` das Preset `Neues Projekt anlegen` nutzen.
   - Beispiel: `Baue ein kleines Release-Check-Tool fuer Maintainer`.
   - Fuer bestehende Repositories danach `Projekt weiterentwickeln` waehlen.

Erfolgssignal fuer den Schnellstart:
- Das Dashboard meldet, dass Aufgaben erstellt wurden.
- Das Goal ist verlinkt oder im Board sichtbar.
- Der naechste Schritt ist `Ziel pruefen`, `Aufgaben verfolgen` oder `Ergebnisse ansehen`.
- Bei `Neues Projekt anlegen` sind Blueprint, initiales Backlog und naechste sichere Schritte im Goal sichtbar.

Wenn der Browser keine Verbindung bekommt, pruefe zuerst `docker compose ps` und die Logs des Hub- und Frontend-Containers.

Offizieller UI-Standardweg: `docs/golden-path-ui.md`.

### F) Docker-Quickstart ohne Ollama (ein auslieferbares Image)

Dieser Pfad baut **ein einziges auslieferbares Image** (`Dockerfile.quickstart-no-ollama`) und startet Hub, Worker und Angular-Frontend daraus.

Build:

```bash
docker build -f Dockerfile.quickstart-no-ollama -t ananta-quickstart-no-ollama:local .
```

Basis-Start (Hub + Worker + Frontend):

```bash
docker compose -f docker-compose.base.yml -f docker-compose.quickstart-no-ollama.yml up -d --build
```

Fullstack aus demselben Image (zusaetzlich Evolver, DeerFlow, ml-intern Worker):

```bash
docker compose -f docker-compose.base.yml -f docker-compose.quickstart-no-ollama.yml -f docker-compose.single-image-fullstack.yml up -d --build
```

Provider auf **LM Studio**:

```bash
DEFAULT_PROVIDER=lmstudio LMSTUDIO_URL=http://host.docker.internal:1234/v1 docker compose -f docker-compose.base.yml -f docker-compose.quickstart-no-ollama.yml -f docker-compose.single-image-fullstack.yml up -d --build
```

Provider auf **OpenAI API**:

```bash
DEFAULT_PROVIDER=openai OPENAI_API_KEY=<SECRET> OPENAI_URL=https://api.openai.com/v1/chat/completions docker compose -f docker-compose.base.yml -f docker-compose.quickstart-no-ollama.yml -f docker-compose.single-image-fullstack.yml up -d --build
```

## CodeCompass Vector Encoding

Ananta komprimiert den Embedding-Index des CodeCompass optional mit verlustbehafteter Quantisierung — transparent, auditierbar und ohne externe Abhängigkeiten.

| Modus | Kompression | Max-Fehler | Status |
|---|---|---|---|
| `off` / `float32` | 1× | 0 | Standard |
| `float16` | 2× | ~0.0002 | stabil |
| `int8` | 4× | ~0.004 | stabil |
| `symmetric4bit` | 8× | ~0.07 | experimentell |
| `turboquant_mse_experimental` | 8× | ~0.07 | Forschung |

**Aktivieren** (`.env` oder Umgebungsvariable):

```bash
CODECOMPASS_VECTOR_ENCODING_MODE=int8
CODECOMPASS_VECTOR_ENCODING_FALLBACK_POLICY=fallback_float32
```

**Demo** (keine API, kein Netz):

```bash
python scripts/demo_vector_encoding.py
```

**Qualitätsmetriken und Rollout-Regeln**: `docs/worker/codecompass-vector-quantization-metrics.md`  
**Architektur**: `docs/architecture/transformer-feature-provider.md`, `docs/architecture/agent-feature-provider.md`  
**Forschung**: `docs/research/turboquant-for-codecompass.md`
