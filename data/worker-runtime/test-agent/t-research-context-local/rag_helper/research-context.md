Artefakt-Kontext:
- Artifact 32f61e9c-e496-4578-928d-e9533cab2829 (README.md):
# Hello
artifact body

Knowledge-Kontext:
- Collection research-docs:
  - README.md: knowledge chunk about retries

Repo-Kontext:
- Repo-Scope README.md: file
# Ananta

Ananta ist eine kontrollierte Hub-Worker-Plattform fuer goal-basierte Agentenarbeit. Du beschreibst ein Ziel; der Hub plant, priorisiert und delegiert Aufgaben, Worker fuehren die Arbeit in getrennten Laufzeitkontexten aus, und Ergebnisse werden ueber Pruefung und Artefakte nachvollziehbar gemacht.

Der Kern ist bewusst nicht "ein Chatbot mit Tools", sondern ein steuerbares System fuer:

- Goal -> Plan -> Task -> Execution -> Verification -> Artifact
- Hub-kontrollierte Orchestrierung statt Worker-zu-Worker-Automation
- Docker-basierte Hub- und Worker-Laufzeiten
- reproduzierbare Releases, CI-Gates und Security-/Governance-Regeln

| Einstieg | Fuer wen | Link |
| --- | --- | --- |
| Direkt ausprobieren | lokale Nutzer und Reviewer | [Schnellstart](#schnellstart-in-5-minuten) |
| Wofuer Ananta offiziell steht | Produkt-/Projekt-Orientierung | [Kern-Use-Cases](docs/use-cases.md) |
| Offizieller UI-Standardweg | Erstnutzer und Demos | [UI Golden Path](docs/golden-path-ui.md) |
| Offizieller CLI-Standardweg | lokale Nutzer und Reviewer | [CLI Golden Path](docs/golden-path-cli.md) |
| Offizieller Release-Standardweg | Maintainer und Betreiber | [Release Golden Path](docs/release-golden-path.md) |
| Passendes Produktprofil waehlen | Demo, Trial, Team oder Security-Kontext | [Produktprofile](docs/product-profiles.md) |
| Architektur verstehen | technische Reviewer | [Architektur](#architektur) |
| Release bewerten | Maintainer und Betreiber | [Release und Governance](#release-und-governance) |
| API nutzen | Integratoren | [Einfache CLI- und API-Beispiele](#einfache-cli--und-api-beispiele) |

## Kern-Use-Cases (offiziell)

Ananta fokussiert sich bewusst auf eine kleine Menge reproduzierbarer Kernanwendungsfaelle (3-5), damit Einstieg, Demo, Benchmarks und Produktprofile auf derselben Basis stehen.

- Repository verstehen
- Bugfix planbar und testbar machen
- Start/Deploy diagnostizieren (Compose/Health/Logs)
- Change Review (Risiken, Tests, Governance)
- Gefuehrte Goal-Erstellung fuer Erstnutzer

Details: `docs/use-cases.md`.

## Schnellstart in 5 Minuten

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
   - Im Arbeitsbereich `Planen` waehlen und ein Ziel eingeben, zum Beispiel: `Analysiere dieses Repository und schlage die naechsten Schritte vor`.
   - Alternativ zuerst die Demo-Vorschau im Dashboard ansehen.

Erfolgssignal fuer den Schnellstart:
- Das Dashboard meldet, dass Aufgaben erstellt wurden.
- Das Goal ist verlinkt oder im Board sichtbar.
- Der naechste Schritt ist `...
