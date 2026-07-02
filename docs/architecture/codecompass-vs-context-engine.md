# CodeCompass vs. Augment Context Engine — Technischer Vergleich

Erstellt: 2026-07-02
Internes Strategiedokument — nicht für externe Veröffentlichung.
Bezug: COSMOS-000

---

## 1. Einordnung

Die **Augment Context Engine** ist der Knowledge-Graph- und Retrieval-Layer innerhalb von Cosmos.
Sie entspricht konzeptionell dem, was **CodeCompass** in Ananta übernimmt: strukturierter Kontext
für Agentenläufe, Symbol-/Datei-/Beziehungssuche, Rangordnung und Kompression für LLM-Prompts.

Beide Systeme sind Teilkomponenten einer größeren Plattform. Der Vergleich hilft,
CodeCompass-Lücken gegen einen konkreten Referenzpunkt zu definieren — nicht als
Imitationsvorlage, sondern als Gap-Maßstab.

---

## 2. Wahrscheinliche Stärken der Augment Context Engine

Einschätzung auf Basis öffentlicher Informationen. Keine interne Dokumentation verfügbar.

### Live Knowledge Graph

Fortlaufend aktualisierter Graph aus Dateien, Symbolen, Funktionen, Services, APIs,
Datenbanken, Events und Tests. Indexierung läuft vermutlich inkrementell im Hintergrund,
nicht als einmaliger Build-Schritt.

### IDE-Aktivitätskontext

Aktuelle geöffnete Dateien, Cursor-Position, aktiver Branch und lokale Änderungen fließen
als Ranking-Signal ein. Kontext wird nicht nur aus dem Repo-Stand berechnet, sondern
aus dem Arbeitskontext des Entwicklers.

### History-Integration

Commit-History, PR-Reviews, Issues und Designentscheidungen sind als Kontextsignal
nutzbar. Ältere Entscheidungen können damit erklärbar gemacht werden.

### Kontinuierliche Indexierung

Keine manuelle Re-Indexierung nötig. Graph-Updates folgen Code-Commits automatisch.

### Confidence und Freshness pro Treffer

Jeder Kontexttreffer hat vermutlich Metadaten zur Aktualität und Relevanz-Einschätzung.
Veraltete oder tote Codepfade werden abgewertet.

### Cross-Repo-Fähigkeit

Beziehungen über mehrere Repositories und Services hinweg modellierbar — relevant für
Monorepos und Microservice-Architekturen.

---

## 3. CodeCompass aktuell

### Stärken

- **Erklärbarer lokaler Graph** — Domain Map, Funktions-/Symbol-Graph mit nachvollziehbaren
  Quellen; kein opakes Embedding-Retrieval ohne Rückverfolgung
- **Symbol- und Funktionssuche** — gezielte Abfragen nach Namen, Typen, Pfaden
- **Domain Map** — fachliche Domänen und deren Dateizuordnung als explizites Modell
- **RAG-Integration** — Vektor-basiertes Retrieval als ergänzender Kanal
- **Policy-Scope-Aware** — Kontext wird gegen erlaubte Pfade gefiltert
- **Kein Cloud-Zwang** — lokale Indexierung ohne externe Abhängigkeit

### Aktuelle Lücken

| Fähigkeit | Status |
|---|---|
| Live-Update (inkrementell) | Fehlt — Indexierung ist kein kontinuierlicher Prozess |
| Cross-Repo-Analyse | Fehlt |
| Confidence/Freshness pro Treffer | Fehlt — kein Metadatum-Modell pro ContextItem |
| Active/Deprecated-Erkennung | Fehlt — kein Statusmodell für Code-Aktivität |
| History Context (Git/PR) | Fehlt — kein HistoryProvider-Port |
| IDE-Aktivitätskontext | Fehlt — kein WorkContext-Signal |
| Context Curation Pipeline | Fehlt — kein expliziter Retrieve→Rank→Compress→Trace-Fluss |
| Evidence-basierte Erklärung | Partiell — Quelle vorhanden, aber kein strukturiertes Evidence-Modell |

---

## 4. Konkreter Entwicklungsplan CodeCompass

Reihenfolge nach Abhängigkeiten und Nutzwert:

### Phase 1: Knowledge Graph Schema

Versioniertes Schema für Knoten (file, function, class, api_endpoint, domain_concept, ...)
und Kanten (calls, imports, tests, publishes, deprecated_by, ...). Jede Kante erhält
Quelle, Confidence und Freshness als Pflichtfelder. Bestehende Domain Map und Symbol-Graph
werden ins Schema migriert, nicht ersetzt.

Abhängigkeit: keine.
Risiko: Schema-Änderungen brechen bestehende Abfragen — Migrationspfad muss Teil des Schemas sein.

### Phase 2: Context Curation Pipeline

Explizite Schritte statt impliziter Trefferliste:
`retrieve_candidates → apply_policy_filter → deduplicate → rank_by_relevance →
rank_by_freshness → rank_by_active_status → compress_snippets → attach_evidence →
fit_context_budget → emit_trace`

Jeder verworfene Treffer bekommt einen Grund. Budget-Überschreitungen führen zu
dokumentierter Kompression, kein blindes Abschneiden.

Abhängigkeit: Knowledge Graph Schema für strukturierte Eingaben.

### Phase 3: Confidence/Evidence-Modell

`ContextItem` erhält Pflichtfelder: `evidence`, `confidence`, `freshness`, `provider`,
`policy_status`, `reason`. Confidence wird aus Signalen berechnet (Call-Graph-Erreichbarkeit,
Testabdeckung, Commit-Aktualität), nicht als LLM-Schätzung.

Abhängigkeit: Context Curation Pipeline als Rahmen.

### Phase 4: Active/Deprecated-Erkennung

Statusmodell: `active | likely_active | unknown | deprecated | dead_candidate | risky`.
Signale: Call-Graph-Erreichbarkeit, Testabdeckung, Commit-Recency, Deprecation-Annotations,
CI/Build-Referenzen. Manuelle Overrides möglich und auditierbar.

Abhängigkeit: Knowledge Graph Schema + Confidence-Modell.

### Phase 5: History Context

`HistoryProvider`-Port für Git-Commits, PRs, Issues, ADRs. Treffer haben Datum, Autor,
betroffene Dateien und Confidence. Veraltete History wird nicht höher bewertet als
aktueller Code. Per Projekt deaktivierbar.

Abhängigkeit: Knowledge Graph Schema.

### Phase 6: Cross-Repo (optional, langfristig)

`RepoBoundary`-Modell definiert, welche Repos gemeinsam analysiert werden dürfen.
Cross-Repo-Kanten haben expliziten Permission-Status. Fehlende Rechte erzeugen
redigierte Platzhalter statt Datenleak. Funktioniert auch mit nur einem Repo.

Abhängigkeit: Knowledge Graph Schema + Active/Deprecated-Erkennung stabil.

---

## 5. Was CodeCompass bewusst nicht anstrebt

- **Automatische Remote-Indexierung** sensibler Repositories ohne explizite Freigabe
- **IDE-Plugin als primärer Zugang** — Ananta ist CLI/API-first
- **Blackbox-Embedding ohne Rückverfolgbarkeit** — Erklärbarkeit bleibt Kern-Invariante
- **Cosmos-Kompatibilität** — CodeCompass ist kein Context-Engine-Ersatz, sondern ein
  eigenständiges, erklärbares System mit anderen Prioritäten

---

*Verwandte Dokumente:*
- `docs/architecture/ananta-vs-cosmos-gap-analysis.md`
- `docs/architecture/ananta-roadmap.md`
- `docs/architecture/codecompass-worker-boundaries.md`
- `docs/architecture/source-pack-codecompass-architecture.md`
