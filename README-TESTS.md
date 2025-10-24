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



## Datenbank- und Einstellungsisolation für Tests

Um zu gewährleisten, dass nach Unit-/Integrationstests (pytest) und insbesondere nach Playwright E2E-Tests die Datenbank und Einstellungen unverändert bleiben, nutzt das Projekt konsequent Test-Isolation:

- Pytest:
  - conftest.py erzwingt eine eigene Test-Datenbank (ananta_test) und initialisiert das Schema vor dem Testlauf. Nach dem Lauf wird die Test-DB wieder gelöscht (Best-Effort). Konfigurationsdateien werden pro Test in temporäre Kopien umgeleitet (ANANTA_CONFIG_PATH), und der in-memory Zustand des Controllers wird nach jedem Test zurückgesetzt.
- Playwright (E2E):
  - Für lokale E2E-Läufe startet Playwright den Controller-Server mit gesetzten Umgebungsvariablen TEST_MODE=1, ENABLE_E2E_TEST_MODELS=1 und E2E_ISOLATE_DB=1 (siehe frontend/playwright.config.js). Dadurch verwendet der Server automatisch eine separate E2E-Datenbank (Suffix _e2e) und berührt die Haupt-DB nicht.
  - Beim E2E-Lauf via docker-compose verwenden Controller und AI-Agent E2E_ISOLATE_DB=1 (siehe docker-compose.yml), sodass alle schreibenden Vorgänge in die isolierte E2E-DB gehen. Die Hauptdatenbank (ananta) bleibt unverändert.
  - Ein explizites Löschen der E2E-DB nach dem Lauf ist nicht zwingend erforderlich, da sie getrennt von der Produktions-/Entwicklungsdatenbank ist. Falls gewünscht, kann man die E2E-DB jederzeit manuell entfernen (z. B. DROP DATABASE ananta_e2e).

Hinweise:
- Die zentrale Logik zur Auswahl der DB-URL liegt in src/db_config.py. Sie erkennt E2E/CI-Flags automatisch und wechselt auf die _e2e-Datenbank, sofern nicht per E2E_ISOLATE_DB=0 deaktiviert.
- Möchtest du die Isolation ausnahmsweise ausschalten, setze E2E_ISOLATE_DB=0 (oder false/no). Für Playwrights lokalen WebServer kannst du außerdem E2E_DATABASE_URL explizit setzen.

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


## E2E-Tests: Keine dauerhaften Änderungen (Cleanup-Policy)

- Ziel: E2E-Tests dürfen den bestehenden Zustand nicht verändern. Sie dürfen ausschließlich eigene Testdaten hinzufügen und müssen diese am Ende des Tests wieder entfernen. Als einzige dauerhafte Artefakte sind Test-Logs erlaubt.
- Vorgehen:
  - Ressourcen mit eindeutigem Test-Prefix/ID anlegen (z. B. `e2e-<timestamp>`).
  - Nach Abschluss des Tests: ausschließlich die zuvor angelegten Ressourcen wieder entfernen.
  - Keine Bearbeitung existierender Einträge (keine Updates), nur Add/Remove der eigenen.
- Umsetzung im Projekt:
  - `frontend/e2e/endpoints.spec.js` wurde so angepasst, dass der Test nur einen neuen Endpunkt mit eindeutiger ID hinzufügt und genau diesen am Ende wieder löscht. Bestehende Endpunkte bleiben unverändert.
  - `frontend/e2e/tasks.spec.js` verwendet eindeutig benannte Tasks, die vom AI-Agent verarbeitet und aus der Liste entfernt werden. So bleiben keine Tasks übrig. In der Docker-Testumgebung ist der Agent aktiv, sodass die Bereinigung automatisch erfolgt.
- Hinweise:
  - Die Test-Umgebung injiziert Modellnamen (`m1`, `m2`) über `ENABLE_E2E_TEST_MODELS=true`, damit das Hinzufügen von Endpunkten deterministisch funktioniert.
  - Falls neue E2E-Tests hinzugefügt werden, ist diese Policy zu befolgen. Gegebenenfalls Hilfsfunktionen (Prefix-Generator, Cleanup) in `frontend/e2e/` anlegen und verwenden.



## E2E-Datenschutz und Aufräum-Regeln

Wichtig: Die Playwright E2E-Tests dürfen keine bestehenden Daten in der Datenbank verändern. Tests müssen ihren eigenen Zustand (bis auf Logs) stets selbst aufräumen und den ursprünglichen Zustand wiederherstellen.

Verbindliche Regeln:
- Tests erzeugen ausschließlich eigene Testdaten mit eindeutigem Präfix, z. B. `e2e-...`.
- Tests ändern oder löschen keine bereits vorhandenen Einträge (z. B. bestehende Agents). Stattdessen erstellen sie temporäre Einträge und entfernen NUR diese wieder.
- Bulk-/Clear-Operationen sind in der Produktivumgebung verboten. Der Endpunkt `DELETE /controller/status` ist nur in Test-Umgebungen nutzbar (`TEST_MODE=1`).
- Für eventuelle Test-Cleanups existiert `DELETE /api/tasks/<id>`, der ausschließlich `e2e-task-...`-Tasks löscht und nur bei `ENABLE_E2E_TEST_MODELS=1` aktiv ist.

Empfohlene Umgebungsvariablen für E2E:
- `TEST_MODE=1`
- `ENABLE_E2E_TEST_MODELS=1`
- `PLAYWRIGHT_BASE_URL` (optional, Standard: `http://localhost:8081`)
- `E2E_DATABASE_URL` (optional; bei Bedarf separate Test-DB angeben, diese wird beim Start des Test-Webservers als `DATABASE_URL` gesetzt)

Playwright-Start (bereits vorkonfiguriert):
- Der Frontend-Playwright-Runner startet den Controller für E2E mit `TEST_MODE=1` und `ENABLE_E2E_TEST_MODELS=1` (siehe `frontend/playwright.config.js`).
- Falls `E2E_DATABASE_URL` gesetzt ist, wird diese als `DATABASE_URL` für den Controller verwendet.

Konventionen in Tests (Beispiele):
- Namen mit Präfix erzeugen: `const name = `e2e-${Date.now()}`;`
- Nach dem Test gezielt entfernen: nur den zuvor erzeugten Eintrag selektieren und löschen; keine Sammel-Löschungen über ganze Tabellen/Listen.

Damit stellen wir sicher, dass E2E-Tests reproduzierbar und nebenläufig sind, ohne bestehende Daten zu verändern.
