# Ananta Playwright-Tests

Es gibt drei Möglichkeiten, die E2E-Tests auszuführen:

## Testumgebung

Die Playwright-Tests setzen eine funktionierende Node.js-Umgebung (>=18) voraus. Für reproduzierbare Ergebnisse empfiehlt sich die Nutzung der bereitgestellten Docker-Container.
Dieser Stack nutzt die Umgebungsvariable `RUN_TESTS` um Tests innerhalb des Controller-Containers zu aktivieren oder zu überspringen.

Stelle sicher, dass Docker installiert ist und die Ports `8081` (Controller) sowie `9444` (Playwright) frei sind.

## 1. Tests im Controller-Service ausführen

Setze die Umgebungsvariable `RUN_TESTS=true` im Controller-Service in der docker-compose.yml:

```yaml
controller:
  environment:
    - RUN_TESTS=true
    # andere Umgebungsvariablen...
```

Dann starte den Service:

```bash
docker-compose up controller
```

Die Tests werden während des Starts ausgeführt, bevor der Controller bereit ist.

## 2. Separaten Playwright-Service verwenden

Diese Option verwendet einen dedizierten Docker-Container für die Tests:

```bash
docker-compose up playwright
```

Die Tests werden ausgeführt und der Container beendet sich danach.

## 3. Tests lokal ausführen

Diese Option verwendet das Shell-Skript, um Tests direkt auf dem Host auszuführen:

```bash
chmod +x run-tests.sh
./run-tests.sh
```

**Hinweis:** Stelle sicher, dass der Controller auf Port 8081 läuft, bevor du die Tests ausführst.

## Debugging von Tests

Für die Fehlersuche bei den Tests kannst du:

1. Die Logausgabe des Containers überprüfen
2. Die Umgebungsvariable `DEBUG=pw:api` setzen, um detaillierte Playwright-Logs zu erhalten
3. Bei lokaler Ausführung mit `PWDEBUG=1` den Playwright-Inspector nutzen

## Docker-Hinweis

Sowohl der Controller- als auch der Playwright-Service basieren auf dem offiziellen Playwright-Image und bringen alle benötigten Browser mit. Nach Abschluss der Tests können Container mit `docker-compose down` entfernt werden.



## Python Unit-Tests

Für schnelle Unit-Tests (ohne Browser) ist Pytest vorgesehen.

- Installation: `pip install -r requirements.txt`
- Ausführen: `pytest -q`

Die Datei `tests/test_http_client.py` prüft Erfolgsfälle, JSON- und Text-Antworten sowie Retry/Timeout-Verhalten für `common/http_client.py`.



# QA Teststrategie und Automatisierung (Ergänzung)

Hinweis: Diese Strategie ergänzt die bestehenden Unit-/E2E-Tests und beschreibt zusätzlich Akzeptanzkriterien, Priorisierung und Fehlerklassifikation. Alle Beispiele sind so gewählt, dass sie lokal und in CI lauffähig sind (falls Abhängigkeiten fehlen, werden Tests sauber übersprungen).

## 1) common/http_client.py

- Unit-Tests (pytest)
  - Erfolgsfälle: JSON- und Text-Antworten (bereits vorhanden)
  - Fehler-/Retry/Timeout: Netzwerkfehler (URLError), Timeout-Weitergabe, Retry bis Erfolg, Retry bis Abbruch (None)
  - JSON/Non-JSON: Ungültiges JSON fällt auf Text zurück
  - Header/Form: Merge von Headern, Formular-Encoding bei form=True
- Integrationstests
  - Optional: Gegen einen lokalen Testserver (z. B. http.server) – in CI i. d. R. gemockt (nicht zwingend nötig)
- Edge-Cases
  - Sehr kleine/leer Antworten, große Payloads, 204 No Content
  - Ungewöhnliche Header (z. B. Content-Type fehlt)
- Mock-Daten
  - Monkeypatch von urllib.request.urlopen; _MockResponse für Bytes-Payload
- Automatisierung (pytest)
  - Befehl: `pytest -q tests/test_http_client.py`
- Akzeptanzkriterien
  - Retries erfolgen in der konfigurierten Anzahl und warten zwischen Versuchen
  - Timeout-Parameter wird an urlopen durchgereicht
  - JSON wird korrekt geparst; bei Fehlern wird Rohtext zurückgegeben
  - Bei permanenten Fehlern wird None zurückgegeben
- Test-Priorisierung
  - Hoch: Fehler-/Retry/Timeout, JSON/Non-JSON
  - Mittel: Header/Form-Fälle
- Fehlerklassifikation
  - Blocker: Falsche Retry/Timeout-Verarbeitung, Exceptions ungefangen
  - Critical: Falscher Body-/Header-Aufbau bei POST
  - Minor: Log-/Warnmeldungen fehlen

## 2) Controller-/Agent-Endpunkte (/stop, /restart, /set_theme, /issues, /ui)

Aktueller Stand im Repo:
- Agent: `/stop`, `/restart`, `/logs`, `/tasks` vorhanden (agent/ai_agent.py)
- Controller: Diverse Routen vorhanden (controller/controller.py, src/controller/routes.py), jedoch keine expliziten `/set_theme`, `/issues`, `/ui`-Backends – die UI wird unter `/ui/` vom Frontend bedient.

- Unit-/Integrationstests (pytest, Flask test_client)
  - Agent: Tests für `/stop` (Flag setzen) und `/restart` (Flag löschen); `/logs` und `/tasks` liefern erwartete Daten (bereits ergänzt in tests/test_agent_endpoints.py)
  - Controller: Bereits vorhandene Tests (tests/test_controller_endpoints.py) nutzen PostgreSQL und werden übersprungen, wenn `psycopg2` fehlt
  - Für fehlende Endpunkte `/set_theme`, `/issues`, `/ui` Teststrategie definieren (pending):
    - `/set_theme`: 200 bei gültigem Theme, 400 bei ungültigem, Persistenz in Config
    - `/issues`: Liste/Filter/Suche von Issues; 201 bei Anlegen, 400 bei Validation Errors
    - `/ui`: Statische Auslieferung; 200 und Sicherheitsheader
- Edge-Cases
  - Ungültige Namen/Parameter (zu lang, leer), leere Tabellen, Pagination-Grenzen
- Mock-Daten
  - Direkte Inserts in Test-DB (siehe vorhandene Tests) oder Mocks, falls DB fehlt (Tests sauber skippen)
- Automatisierung
  - Befehl: `pytest -q tests/test_agent_endpoints.py tests/test_controller_endpoints.py`
- Akzeptanzkriterien
  - `/stop` setzt Flag `agent.flags(name='stop', value='1')`, `/restart` entfernt es
  - `/logs` listet logs des Agenten, `/tasks` listet agent-/globale Aufgaben
  - Sicherheitsheader werden durch Controller gesetzt (X-Frame-Options, CSP, …)
- Test-Priorisierung
  - Hoch: `/stop`, `/restart`, `/logs`, `/tasks`
  - Mittel: `/ui` (Sicherheitsheader), `/issues` (falls implementiert)
  - Niedrig: `/set_theme` (falls implementiert, UI-nah)
- Fehlerklassifikation
  - Blocker: Flags falsch gesetzt/gelöscht, 5xx bei Standardpfaden
  - Critical: Sicherheitsheader fehlen
  - Minor: Validierungsfehlertexte unklar

## 3) E2E-Tests (Playwright) – Dashboard-Flows

- Flows (geplant; teils bereits vorhanden in `frontend/e2e/*.spec.js`):
  - Logs anzeigen: Navigieren zu `/ui/`, Logs-Panel sichtbar, Inhalte laden
  - Task ausführen: Task hinzufügen, in Liste sichtbar, Verarbeitung (Polling) und Abschluss
  - Theme wechseln: UI-Theme-Umschalter, Persistenz prüfen (pending – falls UI-Control existiert)
  - Agent stoppen/neu starten: Buttons im UI (falls vorhanden) rufen `/stop`/`/restart` auf und Status ändert sich (pending)
- Cross-Browser
  - Chromium, Firefox, WebKit sind in `playwright.config.js` aktiviert
- Automatisierung
  - Befehl: `npm --prefix frontend run test:e2e`
  - Environment: `PLAYWRIGHT_BASE_URL`, `PLAYWRIGHT_SKIP_WEBSERVER`
- Akzeptanzkriterien
  - Flows laufen stabil in den 3 Browsern
  - UI lädt innerhalb von 2s (smoke), kritische Aktionen < 3s
- Test-Priorisierung
  - Hoch: Task-Fluss, Logs
  - Mittel: Stop/Restart
  - Niedrig: Theme (kosmetisch)
- Fehlerklassifikation
  - Blocker: UI unbenutzbar, Navigation scheitert
  - Critical: Aktionen hängen oder führen zu falschem Backend-Zustand
  - Minor: Styling, non-blocking Konsolenfehler

## 4) Cross-Browser & Lasttests (Smoke/Stress) mit Schwellenwerten

- Cross-Browser: Playwright-Projekte für Chromium/Firefox/WebKit aktiviert
- Smoke-Checks: Startseite `/ui/` antwortet < 2s; `/config` p95 < 300ms (Low Load)
- Last (leichtgewichtig in pytest): 20 gleichzeitige Requests auf `/config` für 10s, Fehlerquote == 0, p95 < 500ms (konfigurierbar)
- Ausführung
  - Smoke: `pytest -q -m smoke` (falls Marker verwendet)
  - Load (optional, per Env aktiviert): `RUN_LOAD_TESTS=1 pytest -q tests/test_load_smoke.py`

## 5) Markierungen, Skips und Stabilität

- DB-abhängige Tests werden automatisch übersprungen, wenn `psycopg2` nicht verfügbar ist
- Load-Tests standardmäßig übersprungen; Aktivierung via `RUN_LOAD_TESTS=1`
- Playwright: Cross-Browser aktiviert; in CI ggf. per `PLAYWRIGHT_PROJECT` einschränken

## 6) Befehle

- Python-Unit/Integration: `pytest -q`
- Nur http_client: `pytest -q tests/test_http_client.py`
- Agent/Controller: `pytest -q tests/test_agent_endpoints.py tests/test_controller_endpoints.py`
- E2E: `npm --prefix frontend run test:e2e`

## 7) Angehobene Testabdeckung

- Neue Unit-Tests für http_client decken Timeouts, Header/Form und JSON-Fehler ab.
- Integration Agent-Endpunkte bereits vorhanden und erweitert.
- Playwright ist cross-browser konfiguriert.
- Leichter Loadtest ist vorbereitet und per Env aktivierbar.
