Artefakt-Kontext:
- Artifact 116277a5-5d95-4981-a50a-ac47109f8285 (README.md):
# Hello
artifact body

Knowledge-Kontext:
- Collection research-docs:
  - README.md: knowledge chunk about retries

Repo-Kontext:
- Repo-Scope README.md: file
# Ananta

[![Quality And Docs](https://github.com/ananta888/ananta/actions/workflows/quality-and-docs.yml/badge.svg)](https://github.com/ananta888/ananta/actions/workflows/quality-and-docs.yml)
[![Backend Isolated Flows](https://github.com/ananta888/ananta/actions/workflows/backend-isolated-flows.yml/badge.svg)](https://github.com/ananta888/ananta/actions/workflows/backend-isolated-flows.yml)
[![Live LLM Smoke](https://github.com/ananta888/ananta/actions/workflows/live-llm-smoke.yml/badge.svg)](https://github.com/ananta888/ananta/actions/workflows/live-llm-smoke.yml)

Ananta ist eine kontrollierte Hub-Worker-Plattform fuer goal-basierte Agentenarbeit. Du beschreibst ein Ziel; der Hub plant, priorisiert und delegiert Aufgaben, Worker fuehren die Arbeit in getrennten Laufzeitkontexten aus, und Ergebnisse werden ueber Pruefung und Artefakte nachvollziehbar gemacht.

Der Kern ist bewusst nicht "ein Chatbot mit Tools", sondern ein steuerbares System fuer:

- Goal -> Plan -> Task -> Execution -> Verification -> Artifact
- Hub-kontrollierte Orchestrierung statt Worker-zu-Worker-Automation
- Docker-basierte Hub- und Worker-Laufzeiten
- reproduzierbare Releases, CI-Gates und Security-/Governance-Regeln

| Einstieg | Fuer wen | Link |
| --- | --- | --- |
| Direkt ausprobieren | lokale Nutzer und Reviewer | [Schnellstart](#schnellstart-in-5-minuten) |
| Ein-Kommando-Installation | lokale Nutzer und Reviewer | [Bootstrap Install](docs/setup/bootstrap-install.md) |
| Wofuer Ananta offiziell steht | Produkt-/Projekt-Orientierung | [Kern-Use-Cases](docs/use-cases.md) |
| Blueprint/Template/Team einfach verstehen | Erstnutzer und Demos | [Blueprint Product Model](docs/blueprint-product-model.md) |
| Standard-Blueprints mit Beispielen | Erstnutzer und Demos | [Standard Blueprints](docs/standard-blueprints.md) |
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
- Existierend...
