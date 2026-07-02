# Ananta — Priorisierte Entwicklungs-Roadmap

Erstellt: 2026-07-02
Abgeleitet aus: Gap-Analyse Ananta vs. Cosmos (COSMOS-000, COSMOS-024)
Internes Strategiedokument.

Grundsatz: Kein Roadmap-Punkt setzt einen Cloud-Provider voraus.
Security und Policy werden nicht ans Ende geschoben.

---

## Must-have — Fundament

### 1. Expert Registry

JSON-basierte, versionierte Registry für wiederverwendbare Expert-Definitionen.
Jede Definition enthält: Name, Zweck, erlaubte Tools, erlaubte Pfade, Modell-Routing-Profil,
Kontext-Strategie, Output-Vertrag und Approval-Regeln.

Abhängigkeiten: keine.
Risiko: Ohne Versionierung werden Expert-Definitionen schwer migrierbar bei Output-Vertrags-Änderungen.
Sicherheit: Experts können keine eigenen Rechte erfinden. Policy-Scope wird durch Hub gesetzt.
Default-Experts laufen mit minimalen Rechten.

---

### 2. Task Artifact Model

Typisierte, versionierte, run_id-gebundene Artefakte mit Policy-Klassifikation.
Typen: `input_snapshot`, `context_bundle`, `worker_prompt`, `worker_output`, `diff_patch`,
`test_report`, `review_report`, `risk_report`, `approval_record`, `final_summary`.
Klassifikation: `public`, `internal`, `sensitive`, `secret_ref`.

Abhängigkeiten: keine.
Risiko: Ohne Typ-System werden Artefakte schwer abfragbar und für Replay unzuverlässig.
Sicherheit: Secret-Inhalte werden nicht als normale Artefakte gespeichert.
Jeder Worker sieht nur erlaubte Artefakte.

---

### 3. Human-in-the-loop Policy Gates

Einheitliche Gate-Infrastruktur für riskante Aktionen: `apply_diff`, `delete_file`,
`run_network_tool`, `send_context_external`, `create_pull_request`, `merge_pull_request`,
`rerun_ci`, `access_secret_ref`, `deploy_or_release`.
Jedes Gate hat Risiko-Level, erforderliche Rolle, Begründung, Artefakt-Referenz und Ablaufdatum.

Abhängigkeiten: Task Artifact Model.
Risiko: Ohne standardisiertes Gate-System entstehen inkonsistente Einzellösungen.
Sicherheit: Abgelehnte Gates blockieren die Aktion hart. Freigaben werden unveränderlich auditiert.

---

### 4. PolicySnapshot per Run

Beim Run-Start wird der aktive Policy-Scope als unveränderlicher Snapshot gebunden.
Enthält: erlaubte Pfade, erlaubte Tools, Gate-Konfiguration, Expert-Version,
Modell-Routing-Profil, Kontext-Budget.

Abhängigkeiten: keine (parallel zu Artifact Model entwickelbar).
Risiko: Ohne Snapshot wirken Policy-Änderungen rückwirkend auf laufende Runs.
Sicherheit: PolicySnapshot ist schreibgeschützt nach Run-Start.

---

### 5. ContextTrace + ToolCallLog

Pro Run: `ContextTrace` (welche Treffer mit welcher Begründung ausgewählt) +
`ToolCallLog` (welche Tool-Calls mit Parametern und Ergebnissen ausgeführt).

Abhängigkeiten: PolicySnapshot.
Risiko: Ohne Trace sind Policy-Verletzungen und Retrieval-Fehler nicht debugbar.
Sicherheit: ToolCallLog enthält keine Secret-Werte. Sensible Parameter werden redigiert.

---

### 6. DiffProposal als einzige Schreib-Vorstufe

Keine Dateiänderung ohne expliziten `DiffProposal`-Artefakt. Worker schlagen vor,
Hub leitet an Gate. Erst nach Freigabe wird der Diff angewendet.

Abhängigkeiten: Task Artifact Model + Human-in-the-loop Gates.
Risiko: Kern-Invariante — ohne diese Regel können Workers direkt ins Dateisystem schreiben.
Sicherheit: Kein automatischer Apply ohne explizite Freigabe. DiffProposal ist unveränderlich nach Erstellung.

---

## Should-have — Nächste Ausbaustufe

### 7. Agent Runtime State Machine

Zustandsmodell: `created → queued → planning → waiting_for_context →
waiting_for_approval → running → verifying → (failed | cancelled | completed)`.
Resume nur bei idempotenten oder freigegebenen Schritten. Ungültige Übergänge werden rejected.

Abhängigkeiten: Expert Registry + Task Artifact Model.
Risiko: Ohne State Machine sind Fehlerzustände und Timeouts nicht zuverlässig behandelbar.
Sicherheit: Cancel und Timeout sind explizite Zustandsübergänge, kein stilles Aufhören.

---

### 8. Context Curation Pipeline

Explizite Pipeline: `retrieve → apply_policy_filter → deduplicate → rank_by_relevance →
rank_by_freshness → rank_by_active_status → compress → attach_evidence →
fit_context_budget → emit_trace`. Jeder verworfene Treffer bekommt einen Grund.

Abhängigkeiten: keine.
Risiko: Ohne explizite Schritte ist Ranking nicht testbar, Budget-Fehler nicht nachvollziehbar.
Sicherheit: Policy-Filter läuft immer vor Ranking, nie danach.

---

### 9. Confidence/Evidence-Modell pro Kontexttreffer

`ContextItem` erhält: `evidence`, `confidence`, `freshness`, `provider`, `policy_status`, `reason`.
Confidence aus Signalen berechnet, keine LLM-Schätzung. Unklare Treffer: `uncertain`.

Abhängigkeiten: Context Curation Pipeline.
Risiko: Ohne Confidence-Modell werden veraltete Treffer gleich wie aktuelle bewertet.

---

### 10. Sandbox-Abstraktion

`SandboxBackend`-Port: `start`, `exec`, `copy_in`, `copy_out`, `diff`, `stop`, `cleanup`.
Backends: `local_process_restricted` (Default), `docker_container` (optional).
`FakeSandbox` für Tests. Netz, Dateisystem, ENV und Ressourcenlimits konfigurierbar.

Abhängigkeiten: Task Artifact Model (Sandbox-Outputs als Artefakte).
Risiko: Docker ist keine harte Pflicht — Default bleibt lokal restriktiv.
Sicherheit: Jede Sandbox-Ausführung wird auditiert. Policy-Verletzungen werden rejected.

---

### 11. Replayable Runs

`ReplayRecord` enthält Run-Metadaten, Expert-Version, Config-Snapshot, PolicySnapshot-Referenz
und Kontext-Bundle-Referenzen. Dry-Run-Modus repliziert ohne schreibende Side Effects.
Action-Replay (mit Schreiboperationen) erfordert eigene Gates.

Abhängigkeiten: Agent Runtime State Machine + PolicySnapshot stabil.
Risiko: Nicht-deterministische externe Antworten müssen als Snapshot oder `non-replayable` markiert werden.

---

## Later — Wenn Fundament stabil

| # | Punkt | Abhängigkeiten | Risiko / Hinweis |
|---|---|---|---|
| 12 | Knowledge Graph Schema (Knoten/Kanten versioniert, Confidence/Freshness pro Kante) | Context Curation Pipeline | Schema-Änderungen brechen Abfragen — Migrationspfad von Anfang an einplanen |
| 13 | Active/Deprecated-Erkennung (Statusmodell: active/deprecated/dead_candidate/risky) | Knowledge Graph + Confidence-Modell | Signale aus Call-Graph, Tests, Commits — keine geratenen Aussagen |
| 14 | GitHub Trigger + PR Draft Expert | Expert Registry + HITL Gates | Webhooks signiert/validiert; kein automatischer Merge |
| 15 | Cross-Repo-Graph | Knowledge Graph Schema stabil | RepoBoundary-Modell; fehlende Rechte → redigierte Platzhalter |
| 16 | History Context (Git/PR/Issues) | Knowledge Graph Schema | Veraltete History nicht höher bewertet als aktueller Code; per Projekt deaktivierbar |
| 17 | Enterprise Governance / RBAC | PolicySnapshot + Audit stabil | Rollenmodell owner/maintainer/reviewer/operator/observer; RBAC ergänzt Default-Deny |

---

## Abhängigkeitsgraph (kompakt)

```
[1 Expert Registry]──────────────────┐
[2 Artifact Model]───────────────────┼──→ [7 Runtime State Machine]──→ [11 Replayable Runs]
[3 HITL Gates] ←──[2]               │
[4 PolicySnapshot]──→ [5 Traces]    │
[6 DiffProposal] ←──[2,3]          └──→ [11]
[8 Context Curation]──→ [9 Confidence]
[10 Sandbox] ←──[2]

[12 Knowledge Graph] ←──[8,9]──→ [13,15,16]
[14 GitHub Trigger] ←──[1,3]
[17 RBAC] ←──[4,5]
```

---

*Verwandte Dokumente:*
- `docs/architecture/ananta-vs-cosmos-gap-analysis.md`
- `docs/architecture/ananta-product-positioning.md`
- `docs/architecture/codecompass-vs-context-engine.md`
- `todos/todo.ananta-cosmos-context-engine-gap-analysis.json`
