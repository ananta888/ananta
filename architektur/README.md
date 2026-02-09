# Architekturplan für Ananta (Hub/Worker-Modell)

Dieses Dokument beschreibt die Architektur des Ananta-Systems, basierend auf dem Hub/Worker-Modell. Ananta ist ein modulares Multi-Agenten-System zur Automatisierung von Softwareentwicklungs-Tasks.

---

## Inhaltsverzeichnis

1. [Systemübersicht](#systemübersicht)
2. [Komponenten](#komponenten)
   - [Hub (Zentrale Steuerung)](#hub-zentrale-steuerung)
   - [Worker-Agent (Ausführung)](#worker-agent-ausführung)
   - [Frontend (Angular-Dashboard)](#frontend-angular-dashboard)
3. [Datenflüsse und Abläufe](#datenflüsse-und-abläufe)
4. [Technologien und Frameworks](#technologien-und-frameworks)
5. [UML-Diagramme](#uml-diagramme)

---

## Systemübersicht

Ananta nutzt ein dezentrales Hub/Worker-Modell. Ein zentraler **Hub** verwaltet Tasks, Templates und die Agenten-Registry. Spezialisierte **Worker-Agenten** registrieren sich beim Hub, nehmen Aufgaben entgegen, generieren mithilfe von LLMs Lösungswege (Shell-Befehle) und führen diese in einer kontrollierten Umgebung aus.

---

## Komponenten

### Hub (Zentrale Steuerung)
- **Rolle:** Der Hub ist das "Gehirn" und das Gedächtnis des Systems. Er wird gestartet, indem der `ai_agent` mit `ROLE=hub` konfiguriert wird.
- **Aufgaben:**
  - **Registry:** Verwaltung der angemeldeten Worker-Agenten (empfängt `/register` Anfragen).
  - **Task-Management:** Erstellung, Zuweisung und Statusverfolgung von Aufgaben (Backlog, In Progress, Done).
  - **Template-Management:** Bereitstellung von Prompt-Templates für standardisierte Abläufe.
  - **Proxy/Forwarding:** Weiterleitung von Anfragen an den jeweils zugewiesenen Worker.
  - **Monitoring:** Zyklische Prüfung der Erreichbarkeit aller registrierten Worker.
- **Datenhaltung:** SQLModel-Datenbank (Postgres/SQLite) plus JSONL-Logs für Terminalausgaben.

### Worker-Agent (Ausführung)
- **Rolle:** Die ausführende Einheit. Läuft mit `ROLE=worker`.
- **Aufgaben:**
  - **Auto-Registrierung:** Meldet sich beim Hub an und sendet zyklisch seine URL und Kapazitäten (Heartbeat).
  - **LLM-Integration:** Kommuniziert mit Providern wie Ollama, OpenAI oder LM Studio; prüft im Hintergrund die Erreichbarkeit des konfigurierten Providers.
  - **Shell-Execution:** Führt generierte Befehle im lokalen System aus.
  - **Logging:** Schreibt detaillierte Ausführungslogs (`data/terminal_log.jsonl`).
- **Module:**
  - `agent/shell.py`: Sicherer Zugriff auf das Terminal.
  - `agent/llm_integration.py`: Abstraktionsschicht für verschiedene LLM-Anbieter.
  - `agent/routes/tasks/`: Modulare Routen für Task-Management, Ausführung und Scheduling.

### Frontend (Angular-Dashboard)
- **Aufgaben:**
  - Visualisierung des Systemstatus, der Task-Liste und der Live-Logs.
  - Interaktive Steuerung (Tasks erstellen, zuweisen, Schritte manuell triggern).
- **Kommunikation:** Spricht primär mit dem Hub, kann aber für Debugging-Zwecke auch direkt mit Worker-Agenten kommunizieren.

---

## Datenflüsse und Abläufe

1. **Registrierung:** Ein Worker startet und sendet einen POST-Request an `/register` des Hubs.
2. **Task-Erstellung:** Über das Frontend wird ein Task im Hub angelegt.
3. **Zuweisung:** Der Task wird einem registrierten Worker zugewiesen (`/tasks/<tid>/assign`).
4. **Ausführung:**
   - Der Hub empfängt einen `/step/propose` Request.
   - Falls der Task einem Worker zugewiesen ist, leitet der Hub die Anfrage an den Worker weiter.
   - Der Worker fragt das LLM an, generiert einen Befehl und sendet ihn zurück.
   - Nach Genehmigung führt der Worker den Befehl aus und meldet das Ergebnis an den Hub.

---

## Technologien und Frameworks

- **Backend:** Python 3.11+, Flask (als API-Server).
- **Validierung:** Pydantic (für Konfiguration und Request-Modelle).
- **Concurrency:** Threading für Hintergrund-Tasks (Housekeeping, Monitoring, LLM-Check, Auto-Registration).
- **Sicherheit:** Token-basierte Authentifizierung (Bearer-Token) und JWT für Benutzer-Sessions.
- **Frontend:** Angular 18+, komponentenbasiertes Dashboard.

---

## UML-Diagramme

Die Diagramme befinden sich im Ordner `architektur/uml/` und nutzen Mermaid-Syntax:

- [Systemübersicht](uml/system-overview.mmd)
- [Komponenten-Diagramm](uml/component-diagram.mmd)
- [Deployment-Szenario](uml/deployment-diagram.mmd)
- [Produktions-Deployment](uml/production-deployment.mmd)
- [Klassendiagramm](uml/backend-class-diagram.mmd)

---
*Hinweis: Dieses Dokument ersetzt die veraltete Controller-zentrierte Dokumentation.*
