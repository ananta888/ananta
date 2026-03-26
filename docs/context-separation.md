# BE-CTX-771: Separate task working context from project knowledge sources

Ziel
----
Kurzbeschreibung: Einführung klarer Grenzen zwischen kurzfristigem Task-Working-Context (z.B. CLI/Editor-Snippets oder LLM-gestützte dialogkontext) und dauerhaften Projekt-Wissen/RAG-Quellen (z.B. docs, repo, knowledge-bases).

Empfohlene Änderungen
---------------------
- Datenmodell: Ergänze Task-Metadaten um `task_context` (Kurzlebig) und `goal_context` (mittelfristig). Projektwissen bleibt in `rag`-/`knowledge`-Quellen.
- Runtime: Beim Planen und Ausführen werden nur die explizit erlaubten Kontext-Quellen an die LLMs gegeben. Repo-Kontext wird per Feature-Flag oder expliziter Option hinzugefügt.
- API: Neue Felder in GoalCreateRequest: `use_repo_context` (bereits vorhanden) dokumentieren; optional `task_context_ttl` als Hinweis.
- Migration: Alte Endpunkte behalten Verhalten; neue Flags konservativ default-off.

Akzeptanzkriterien
------------------
- Dokumentation vorhanden und Beispiele für Operator-Konfiguration
- Keine API-Breaking-Änderungen
- Konfigurierbarkeit via config.json

Nächste Schritte (Implementierung)
----------------------------------
1. API-Dokumentation aktualisieren (docs/api-goal.md)
2. Backend: klarstellen, welche Felder in Goal/Task-Model benutzt werden (Planungsservice & Task-Serialisierung)
3. Integrationstests (später)

Architekturklarstellung
-----------------------
- `context` am Goal bleibt kurz- bis mittelfristiger Arbeitskontext fuer Planung und Ausfuehrung.
- Repo- oder Wissenskontext bleibt opt-in und wird ueber `use_repo_context` bzw. explizite Knowledge-Quellen aktiviert.
- Dauerhafte Projektkenntnis wird nicht in Worker-zu-Worker-Kanaelen weitergereicht, sondern nur ueber den Hub-konfigurierten Kontextpfad.
- Die UI soll Task-Kontext, Goal-Kontext und durable Knowledge getrennt anzeigen, damit Herkunft und Lebensdauer inspizierbar bleiben.
