# Ananta — Systemkomponenten und was sie tun

Dieses Dokument erklärt die wichtigsten Komponenten des Ananta-Systems.
Es beantwortet Fragen wie „Was ist X?", „Wie funktioniert Y?", „Wo liegt Z?" und
„Erkläre mir die Architektur". Es ist der primäre Einstiegspunkt für
Selbstbeschreibungs-Anfragen an AI-Snake.

---

## Was ist Ananta?

Ananta ist eine lokale, offene Multi-Agenten-Plattform für KI-gestützte
Softwareentwicklung. Das Kürzel steht für
**Autonomous Networked Agents Navigate Trusted Artifacts**.

Das Herzstück ist die Hub-Worker-Architektur: Ein Hub (Kontrollplane) plant,
priorisiert und delegiert Aufgaben. Worker führen die eigentliche Arbeit in
getrennten Laufzeitkontexten aus. Ergebnisse werden als Artefakte gespeichert
und geprüft.

Der vollständige Ablauf ist:
**Goal → Plan → Task → Execution → Verification → Artifact**

---

## Hub

Der Hub ist die Kontrollplane von Ananta. Er läuft als Flask-Python-Prozess
mit `ROLE=hub` im Docker-Container `ananta-ai-agent-hub`.

**Was der Hub tut:**
- Aufgaben anlegen, priorisieren, an Worker delegieren
- Routen, Policy-Gates, Audit-Logs verwalten
- AI-Snake-Chat bereitstellen (der Chat-Bot im Hub)
- CodeCompass-Retrieval für Anfragen koordinieren
- Angular-Frontend mit API versorgen

**Kerncode:** `agent/` — Routen in `agent/routes/`, Services in `agent/services/`

---

## Worker

Worker sind getrennte Ausführungsagenten. Sie führen aus, was der Hub
delegiert: LLM-Aufrufe, Code-Generierung, Analysen, Tool-Ausführungen.

Worker orchestrieren keine anderen Worker. Alle Orchestrierung läuft
über den Hub. Zwei Worker-Instanzen laufen als
`ananta-ai-agent-alpha` und `ananta-ai-agent-beta`.

---

## CodeCompass — Was ist CodeCompass?

CodeCompass ist das **RAG-Indexierungs- und Retrieval-System** von Ananta.
Es ist die Antwort auf die Frage: „Wie findet Ananta relevante Dateien und
Codestellen für eine Anfrage?"

CodeCompass besteht aus zwei Teilen:

### 1. Der Indexer: `rag-helper/`

Der Ordner `rag-helper/` enthält das eigentliche Indexierungs-Tool.
Das Hauptskript ist `rag-helper/codecompass_rag.py`.

Der Indexer analysiert das Repository und erzeugt:
- `rag-helper/out/embedding.json` — Vektor-Embeddings für semantische Suche
- `rag-helper/out/index.jsonl` — Full-Text-Suchindex (FTS)
- `rag-helper/out/cc_graph_index.json` — Symbol- und Abhängigkeitsgraph
- `rag-helper/data/` — Rohdaten und Artefakte
- `rag-helper/research-context.json` — Handoff-Format für Worker-Prompts

Der Indexer versteht Python, TypeScript, Java, C#, Markdown, YAML, SQL und weitere Dateitypen.
Er extrahiert Klassen, Methoden, Felder, Imports und Dokumentation strukturiert.

Der Indexer wird über `RagHelperIndexService` (`agent/services/rag_helper_index_service.py`)
gesteuert und kann per API-Endpunkt oder manuell neu gebaut werden.

### 2. Die Runtime: `agent/codecompass/`

Der Ordner `agent/codecompass/` enthält die Laufzeit-Hilfsfunktionen,
die beim Retrieval (Abfrage-Zeit) eingesetzt werden:

- `domain_scope.py` — Datenmodell für Lese-/Schreibbereiche (DomainScope)
- `domain_scope_resolver.py` — Löst Domain-Hints zu erlaubten Pfaden auf
- `domain_scope_filter.py` — Filtert Chunks auf erlaubte Pfade
- `domain_scope_approval.py` — Genehmigungslogik für schreibende Aktionen

### 3. Die Services: `agent/services/codecompass_*.py`

- `codecompass_vector_retrieval_service.py` — semantische Vektorsuche im Embedding-Index
- `codecompass_context_planner_service.py` — Budget- und Kontextplanung für Retrieval
- `codecompass_retrieval_flag_service.py` — Steuerflags für Retrieval (FTS/Vektor/Graph)
- `codecompass_output_reader.py` — liest und verarbeitet rag-helper-Ausgaben
- `codecompass_reload.py` — Hot-Reload des Index ohne Hub-Neustart

### 4. Retrieval-Routen: `agent/routes/codecompass_*.py`

- `codecompass_domain_scope.py` — API für Domain-Scope-Abfragen
- `codecompass_graph.py` — Graph-Query-Endpunkte
- `codecompass_reload.py` — Reload-Endpunkt

### Zusammenfassung: Was ist CodeCompass?

> CodeCompass = rag-helper (Indexer) + agent/codecompass/ (Runtime) +
> agent/services/codecompass_*.py (Services) + agent/routes/codecompass_*.py (API)
>
> Der rag-helper erzeugt den Index. Die Runtime nutzt ihn bei Anfragen.
> Zusammen ermöglichen sie, dass Ananta bei jeder Frage die passenden
> Dateien und Codestellen aus dem Projekt abruft.

---

## AI-Snake — Was ist AI-Snake?

AI-Snake ist der **Chat-Bot des Hubs**. Er beantwortet Fragen über das
Ananta-Projekt direkt im Chat, ohne eine Worker-Aufgabe anzulegen.

AI-Snake nutzt CodeCompass-Retrieval, um Fragen mit echtem Projektkontext
zu beantworten (RAG = Retrieval-Augmented Generation).

**Kerncode:**
- `agent/routes/snakes_execution_routes.py` — Antwortgenerierung, Retrieval, Trace
- `agent/routes/snakes.py` — Registrierung, Nachrichten, API-Endpunkte
- `agent/routes/ai_snake_config.py` — Konfiguration (Provider, Limits, Flags)

**Was AI-Snake kann:**
- Fragen über den Ananta-Code beantworten (mit CodeCompass-Kontext)
- Sich selbst erklären (dieses Dokument ist dafür der Einstieg)
- Laufende Antwort-Traces anzeigen (Trace-Viewer in der Angular-App)
- Mit verschiedenen LLM-Providern arbeiten (lmstudio, openai, hermes)

---

## Angular-Frontend

Das Angular-Frontend (`frontend-angular/`) ist die Web-Oberfläche von Ananta.
Es läuft im Container `ananta-angular-frontend` und bietet:

- Dashboard, Task-Board, Artifact-Viewer
- AI-Chat-Seite (große Ansicht mit Trace-Viewer)
- AI-Snake-Chat-Panel (kleines Floating-Panel mit Trace-Tab)
- Operator-TUI-Integration

---

## Operator TUI

Das Operator TUI (Terminal User Interface) ist die Kommandozeilen-Oberfläche
für Ananta. Es erlaubt Konfiguration, Monitoring und Bedienung ohne Browser.

---

## Ananta Game

Das Ananta Game ist eine Multiplayer-Strategie-Spielschicht, die als
Architektur-Lernschicht fungiert. Es verbindet Hub-Mechaniken spielerisch
mit dem realen System.

---

## Hybrid-RAG — Wie funktioniert das Retrieval?

Wenn AI-Snake eine Frage beantwortet, läuft das Retrieval so ab:

1. **Retrieval-Profil auswählen** — `agent/services/retrieval_profile_service.py`
   klassifiziert die Frage (Domäne: codecompass, worker, ai_snake, game, ...)
   und wählt ein passendes Suchprofil.

2. **CodeCompass abfragen** — Der RAG-Service (`agent/services/rag_service.py`)
   kombiniert mehrere Engines:
   - Semantische Vektorsuche (LlamaIndex über `rag-helper/out/embedding.json`)
   - Full-Text-Suche (FTS über `rag-helper/out/index.jsonl`)
   - Repository-Map (Symbol-Index aus `agent/game/codecompass_adapter.py`)

3. **Grounded Prompt bauen** — Die gefundenen Chunks werden in einen Prompt
   eingebettet (`_build_grounded_snake_prompt` in `snakes_execution_routes.py`).

4. **LLM aufrufen** — Der Prompt geht an das konfigurierte LLM (lmstudio/hermes/openai).

5. **Antwort zurückgeben** — Die Antwort wird im Chat angezeigt und im Trace-Viewer
   sichtbar gemacht.

---

## Wo liegt was? — Schnellreferenz

| Komponente | Ort |
|---|---|
| CodeCompass Indexer (rag-helper) | `rag-helper/` |
| CodeCompass Hauptskript | `rag-helper/codecompass_rag.py` |
| CodeCompass Runtime | `agent/codecompass/` |
| CodeCompass Services | `agent/services/codecompass_*.py` |
| CodeCompass Index-Verwaltung | `agent/services/rag_helper_index_service.py` |
| AI-Snake Chat-Logik | `agent/routes/snakes_execution_routes.py` |
| AI-Snake Konfiguration | `agent/routes/ai_snake_config.py` |
| Retrieval-Profile | `agent/services/retrieval_profile_service.py` |
| RAG-Service | `agent/services/rag_service.py` |
| Angular-Frontend | `frontend-angular/src/app/` |
| Hub-Hauptprozess | `agent/ai_agent.py` |
| Docker-Konfiguration | `docker/old_way/docker-compose.yml` |
| Nutzer-Einstellungen | `user.json` |
