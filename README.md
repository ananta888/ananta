Projektarchitektur

Das Repository gliedert sich in drei Hauptkomponenten:

    Controller (Flask‑Server)

        Verwaltet Agenten‑Konfiguration, Aufgabenliste, Blacklist und Logs.

        Stellt HTTP‑Endpoints für das Agenten‑Ökosystem, die Weboberfläche und einen optionalen Vue‑Frontend‑Build bereit.

        Konfigurationen werden in data/config.json gespeichert und beim Start mit Standardwerten oder Team‑Vorlagen zusammengeführt.

    AI‑Agent (ai_agent.py)

        Läuft in einer Schleife, ruft über den Controller Konfigurationen ab und generiert anhand von Prompt‑Templates Kommandos.

        Unterstützt mehrere LLM‑Provider (Ollama, LM Studio, OpenAI) mit frei konfigurierbaren Endpunkten.

        Nutzt ModelPool, um gleichzeitige Modellanfragen zu begrenzen, und protokolliert jede Ausführung in separaten Log‑ und Summary‑Dateien.

    Frontend (Vue)

        Einfaches Dashboard zur Anzeige und Steuerung der Agenten.

        Kommuniziert über Fetch‑Aufrufe mit den Flask‑Endpoints.

Zentrale Klassen und Methoden
Modul/Datei	Klasse/Funktion	Zweck
src/agents/base.py	Agent (Dataclass), from_file(path)	Repräsentiert eine Agenten‑Konfiguration; Einlesen aus JSON.
src/agents/__init__.py	load_agents(config_dir)	Lädt mehrere Agenten‑Configs aus einem Verzeichnis.
src/agents/templates.py	PromptTemplates, add(name, tpl), render(name, **kw)	Verwaltung und Formatierung von Prompt‑Vorlagen.
src/controller/agent.py	ControllerAgent – erweitert Agent; Methoden assign_task, update_blacklist, log_status	Verteilt Aufgaben und führt eine Blacklist.
src/controller/routes.py	Blueprint‑Funktionen next_task, blacklist, status	Zusätzliche Controller‑HTTP‑Routen unter /controller.
src/models/pool.py	ModelPool mit register, acquire, release; interne Klasse _QueueEntry	Begrenzt parallele LLM‑Anfragen pro (Provider, Modell).
ai_agent.py	_agent_files, _http_get, _http_post, run_agent	Hilfsfunktionen und Hauptschleife des AI‑Agents.
controller.py	load_team_config, read_config, fetch_issues u. a.	Konfigurationsverwaltung und Hilfslogik des Controllers.
HTTP‑Endpoints

Hauptcontroller (controller.py)
Endpoint	Methode(n)	Beschreibung
/next-config	GET	Liefert die nächste Agenten‑Konfiguration inkl. Aufgaben & Templates.
/config	GET	Vollständige Controller‑Konfiguration als JSON.
/approve	POST	Nimmt vom Agent vorgeschlagene Kommandos an, prüft Blacklist, loggt.
/issues	GET	Holt GitHub-Issues; optionales Enqueue in die Aufgabenliste.
/set_theme	POST	Setzt Dashboard‑Theme per Cookie.
/	GET/POST	HTML‑Dashboard: Pipeline‑Reordering, Task‑Management, Agenten‑Konfiguration.
/agent/<name>/toggle_active	POST	Schaltet controller_active eines Agenten um.
/agent/<name>/log	GET	Liefert Logdatei des Agenten.
/stop / /restart	POST	Setzt oder entfernt stop.flag für den Agenten‑Loop.
/export	GET	Exportiert Logs & Konfigurationsdateien als ZIP.
/ui und /ui/<pfad>	GET	Serviert den gebauten Vue‑Frontend‑Dist‑Ordner.

Blueprint‑Routen (src/controller/routes.py, Prefix /controller)
Endpoint	Methode(n)	Beschreibung
/controller/next-task	GET	Gibt die nächste nicht‑gesperrte Aufgabe zurück.
/controller/blacklist	GET/POST	Liest bzw. ergänzt die Blacklist.
/controller/status	GET	Liefert internen Log‑Status des ControllerAgent.
Zusammenwirken

    Startup

        Controller lädt/initialisiert config.json, ggf. default_team_config.json.

        Bei Bedarf registriert ModelPool Limits für Modelle.

    Agentenlauf

        ai_agent.py ruft zyklisch /next-config ab, baut aus Template & Aufgabe einen Prompt und sendet ihn an den konfigurierten LLM‑Endpoint.

        Ergebnis wird mit /approve verifiziert; das bestätigte Kommando wird lokal ausgeführt und geloggt.

    Dashboard/Frontend

        Vue‑App und HTML‑Dashboard rufen /config, /agent/<name>/log, /stop, /restart usw. auf, um Status anzuzeigen und Eingriffe zu ermöglichen.

    Erweiterbarkeit

        Zusätzliche Agenten‑JSON‑Dateien (über load_agents) und neue Prompt‑Templates lassen sich leicht einbinden.

        ModelPool ermöglicht Konfigurations‑abhängiges Throttling pro Provider/Modell.

Damit bietet das Projekt eine modulare Grundlage für ein mehrstufiges Agenten‑System mit zentraler Steuerung, asynchronen Modellanfragen und einfacher Weboberfläche.