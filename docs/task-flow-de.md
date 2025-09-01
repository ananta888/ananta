# Aufgabenfluss (Task-Logik) von Frontend über Controller zum AI‑Agent

Dieser Leitfaden erklärt die End‑to‑End‑Logik für Aufgaben ("Tasks") anhand des Quellcodes: vom Vue‑Frontend, über den Flask‑Controller und die Datenbankmodelle bis hin zum AI‑Agent. Er enthält direkte Dateipfade und Zeilenverweise, sodass Sie das Verhalten schnell im Code nachvollziehen können.

## Überblick

- Frontend (Vue): Aufgaben werden über das Dashboard angelegt und angezeigt.
- Controller (Flask): Validiert Eingaben, speichert Aufgaben (DB) bzw. in Fallback‑Queue und stellt APIs bereit.
- Datenbank (SQLAlchemy): Persistente Queue `controller.tasks` (Tabelle), inkl. Timestamps für Abruflogik.
- AI‑Agent: Pollt den Controller (/tasks/next) und verarbeitet Aufgaben.

## Frontend: Aufgaben anlegen und anzeigen

Datei: `frontend/src/components/Tasks.vue`

- Laden der Konfiguration (inkl. Aufgabenliste, wenn UI‑Konfiguration diese enthält):
  - Zeilen 48–60: `loadConfig()` ruft `GET /config` auf und setzt `config`.
- Aufgabe hinzufügen:
  - Zeilen 89–118: `addTask()` baut Payload `{ task, agent?, template? }` und ruft `POST /agent/add_task` auf.
  - Bei Erfolg wird erneut `loadConfig()` aufgerufen und die Formularfelder werden geleert.
- Aufgabenliste rendern und einfache UI‑Aktionen (Start, Move, Skip, Edit):
  - Zeilen 6–33: Anzeige vorhandener Tasks aus `config.tasks` (UI‑Kontext). Die eigentliche Agenten‑Queue wird über die Controller‑Endpoints verwaltet (s. unten).

Hinweis: Für die Agenten‑Queue ist vor allem der Endpunkt `/agent/add_task` (Anlage) und `/tasks/next` (Abruf/Verbrauch) relevant. Die Form‑Aktionen, die an `/` posten, betreffen die UI‑eigene Task‑Liste der Konfiguration.

## Controller: Endpunkte und Fallback‑Queue

Datei: `controller/controller.py`

Wesentliche Endpunkte und Hilfsfunktionen:

- POST `/agent/add_task` (Zeilen 766–807)
  - Erwartet JSON `{"task": str, "agent"?: str, "template"?: str}`.
  - Validiert `task` (non‑empty String), `agent`/`template` (max. 128 Zeichen; leer → None).
  - Schreibt in die DB‑Tabelle `controller.tasks` (Modell `ControllerTask`), ruft `s.flush()` um die ID zu erhalten und gibt `{ "status": "queued", "id"?: int }` zurück.
  - Fallback: Wenn DB nicht verfügbar ist, wird `_fb_add()` verwendet (siehe unten), Rückgabe `{ "status": "queued" }`.

- GET `/agent/<name>/tasks` und Alias `/api/agents/<name>/tasks` (Zeilen 816–840)
  - Liefert Aufgaben für den Agenten `<name>` sowie unzugewiesene (`agent IS NULL`), aufsteigend nach `id`.
  - Fallback: `_fb_list(name)` liefert entsprechende Einträge aus der In‑Memory‑Queue.

- GET `/tasks/next` (Zeilen 843–881)
  - Liefert und entfernt die nächste passende Aufgabe für den optional übergebenen Query‑Parameter `agent`.
    - Mit `agent`: filtert `(agent == name) OR agent IS NULL`.
    - Ohne `agent`: bevorzugt unzugewiesene Aufgaben (`agent IS NULL`).
  - Wichtige Logik: „Consume Delay“ – Aufgaben werden erst nach einer Wartezeit ausgeliefert, damit das UI die neu eingereihten Tasks vorher sehen kann.
    - Umgebungsvariable: `TASK_CONSUME_DELAY_SECONDS` (Default: 8 Sekunden), Zeilen 861–869.
    - DB‑Pfad: Filter `created_at <= now() - INTERVAL 'delay seconds'` (Zeilen 868–869), dann `DELETE` der gelieferten Zeile (Zeilen 873–874).
  - Fallback: `_fb_pop_for_agent(agent)` (Zeilen 105–131) nutzt `enqueued_at`‑Zeitstempel und dieselbe Verzögerungslogik.

- GET `/agent/config` (Zeilen 721–763)
  - Dient Agent/Frontend zur Abfrage von `active_agent` und `agents`‑Mapping. Fällt auf JSON‑Dateien zurück, wenn DB leer/unverfügbar ist.

- In‑Memory‑Fallback‑Queue (Zeilen 70–131)
  - `_fb_add(task, agent, template)` (Zeilen 80–89): Speichert Task in eine `deque` inkl. `enqueued_at`‑Timestamp.
  - `_fb_list(name)` (Zeilen 91–97): Listet passende Einträge (Agent == name oder None).
  - `_fb_pop_for_agent(name)` (Zeilen 105–131): Entfernt die erste reife (Delay erfüllt) und passende Aufgabe.

Sicherheitsaspekte:
- Eingabevalidierung (Typen/Längen) in `/agent/add_task` (Zeilen 769–777).
- Sicherheits‑Header via `@app.after_request` in Zeilen 169–186.

## Datenbankmodell: Aufgabenpersistenz

Datei: `src/db/sa.py`

- `ControllerTask` (Zeilen 64–76):
  - Felder: `id SERIAL PK`, `task TEXT NOT NULL`, `agent VARCHAR(128) NULL`, `template VARCHAR(128) NULL`, `created_at TIMESTAMPTZ DEFAULT now()`.
  - Index über `(agent, created_at)` zur effizienten Abfrage.
- `session_scope()` (Zeilen 41–53): Einheitlicher Transaktionsrahmen (Commit/Rollback/Close).

Die Controller‑Endpunkte nutzen dieses Modell, um Aufgaben robust zu persistieren. Bei DB‑Ausfall steht die Fallback‑Queue bereit (siehe oben).

## AI‑Agent: Aufgaben abrufen und verarbeiten

Datei: `agent/ai_agent.py`

- Hauptschleife `main()` (Zeilen 125–159):
  - Liest `CONTROLLER_URL` (Default `http://controller:8081`) und `AGENT_NAME` (Default `Architect`).
  - Optionaler Start‑Delay `AGENT_STARTUP_DELAY` (Default 3s), damit E2E‑Tests den Task vorher sehen.
  - Pollt `GET {CONTROLLER_URL}/tasks/next?agent=<AGENT_NAME>` (Zeilen 143–151).
    - Bei Aufgabe: schreibt einfache In‑Memory‑Logs `Received task: ...` und `Processed: ...` (Zeilen 149–151).
  - Fehlerbehandlung für Verbindungs‑/Andere Fehler; 1 Sekunde Schlaf pro Iteration.

- Flask‑App des Agents `create_app()` (Zeilen 27–122):
  - `GET /health`: Healthcheck.
  - `GET /agent/<name>/log`: Plain‑Text‑Logs (für E2E‑Tests).
  - `POST /stop` / `POST /restart`: Setzen/Löschen eines Flags in `agent.flags` (Schema `agent`).
  - `GET /logs`: Liefert DB‑Logs für den Agenten.
  - `GET /tasks`: Hilfsroute für Tests; zeigt aktuelle Controller‑Konfiguration und die Task‑Queue aus DB.

## Sequenz des Aufgabenflusses

1. Nutzer gibt im Dashboard eine Aufgabe ein und klickt „Add“.
   - Frontend ruft `POST /agent/add_task` mit `{ task, agent?, template? }` auf (`Tasks.vue` Zeilen 89–118).
2. Der Controller validiert die Eingabe und fügt die Aufgabe in `controller.tasks` ein.
   - DB‑Pfad: `ControllerTask` wird persistiert (Zeilen 788–799).
   - Fallback: `_fb_add` speichert in der In‑Memory‑Queue.
3. Das Dashboard ruft erneut `GET /config` auf, um die UI zu aktualisieren.
4. Der AI‑Agent pollt periodisch `GET /tasks/next?agent=<Name>`.
   - Der Controller liefert die nächste passende Aufgabe, jedoch erst nach Ablauf der Verzögerung `TASK_CONSUME_DELAY_SECONDS` (Default 8s), damit die UI die Aufgabe vorher sehen kann.
   - DB‑Pfad: Aufgabe wird nach Auslieferung gelöscht (Zeilen 873–874).
   - Fallback‑Pfad: `_fb_pop_for_agent` entfernt die Aufgabe aus der In‑Memory‑Queue.
5. Der Agent verarbeitet die Aufgabe (z. B. Logs „Received/Processed“) und fährt mit dem nächsten Polling fort.

## Wichtige Umgebungsvariablen

- `TASK_CONSUME_DELAY_SECONDS` (Default: `8`): Wartezeit, bevor Aufgaben an Agenten ausgeliefert werden.
- `CONTROLLER_URL`: Basis‑URL des Controllers für den Agenten (Default `http://controller:8081`).
- `AGENT_NAME`: Name des Agenten, der Aufgaben abruft (Default `Architect`).
- `AGENT_STARTUP_DELAY` (Default: `3`): Startverzögerung des Agents.

## Tests, die den Flow abdecken

- Python: `tests/test_controller_tasks.py`
  - Fügt Task hinzu (`POST /agent/add_task`), listet Tasks (`GET /agent/default/tasks`), ruft `GET /tasks/next?agent=default` ab und prüft, dass die Liste danach leer ist.
- Python (DB‑abhängig, wird ohne Psycopg2 übersprungen): `tests/test_controller_endpoints.py`
  - Setzt `TASK_CONSUME_DELAY_SECONDS=0`, prüft `/next-config`, `/agent/add_task`, `/tasks/next`, Blacklist‑Pfad etc.
- Frontend: `frontend/tests/Tasks.spec.js`
  - Mockt Fetch und prüft, dass `addTask()` die korrekte Payload an `/agent/add_task` sendet und die UI aktualisiert.

## Edge Cases & Verhalten

- Ungültige Eingabe: `task` leer oder falscher Typ → `400 {"error": "invalid_task"}`. Entsprechendes auch für `agent`/`template` (Zeilen 769–777).
- DB nicht verfügbar: Controller fällt auf In‑Memory‑Queue zurück; Verzögerung wird trotzdem beachtet (`_fb_pop_for_agent`).
- Ohne Query‑Parameter `agent`: `/tasks/next` priorisiert unzugewiesene Aufgaben (`agent IS NULL`).
- Performance: Index `(agent, created_at)` beschleunigt Abfragen; konsumierte Aufgaben werden sofort gelöscht.

## Zugehörige Architektur‑Diagramme

- `architektur/uml/system-overview.mmd`
- `architektur/uml/task-approval-sequence.mmd`
- `architektur/uml/deployment-diagram.mmd`

Diese Datei soll als Referenz dienen, um Implementierungen oder Tests schnell mit der tatsächlichen Task‑Logik abzugleichen.