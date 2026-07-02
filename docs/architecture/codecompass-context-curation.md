# CodeCompass Context Curation Pipeline (COSMOS-013)

## Ziel

Aus einer Rohliste von Kandidaten-Treffern wird eine kuratierte, erklärte und
budget-konforme Kontextauswahl. Jeder Schritt ist explizit, testbar und
hinterlässt einen nachvollziehbaren Trace.

---

## Pipeline-Schritte

```
Query → [1] retrieve_candidates → [2] apply_policy_filter → [3] deduplicate
      → [4] rank_by_relevance → [5] rank_by_freshness → [6] rank_by_active_status
      → [7] compress_snippets → [8] attach_evidence → [9] fit_context_budget
      → [10] emit_trace → ContextBundle
```

### [1] retrieve_candidates

Sammelt Treffer aus: Knowledge Graph, Vektor-Index, History Provider, WorkContext (falls aktiv).
Ergebnis: unkuratierte Liste mit Rohtreffer und Metadaten.

### [2] apply_policy_filter

Entfernt alle Treffer aus `denied_paths` oder mit unzureichenden Zugriffsrechten.

```python
@dataclass
class FilterResult:
    kept: list[RawCandidate]
    dropped: list[DroppedCandidate]   # reason_code: "denied_path"
```

Kein Treffer aus `denied_path` passiert diese Stufe. Kein stiller Drop.

### [3] deduplicate

Gleiche Datei/Symbol aus mehreren Quellen → behalte höchsten Score, merge Evidence-Listen.
Deduplizierung anhand `(path, symbol_name, node_id)`.
Verlierer wird als `DroppedCandidate` mit `reason_code: "duplicate"` gespeichert.

### [4] rank_by_relevance

Semantische Ähnlichkeit zur Query (Vektor-Ähnlichkeit oder BM25-Score, 0.0–1.0,
normalisiert über alle Kandidaten).

### [5] rank_by_freshness

`score += freshness * FRESHNESS_WEIGHT` (default: 0.15).
Stale History-Treffer erhalten negativen Freshness-Beitrag.

### [6] rank_by_active_status

Score-Delta nach `CodeStatus`: active +0.2, likely_active +0.1, unknown 0.0,
uncertain -0.1, deprecated -0.3, dead_candidate -0.5, risky 0.0.
Details siehe `codecompass-active-deprecated.md`.

### [7] compress_snippets

Lange Snippets werden auf `max_chars` (default: 800) gekürzt:
zuerst Kommentare/Leerzeilen entfernen, dann hart kürzen mit `truncated=True`.
Kein Abschneiden ohne Markierung.

### [8] attach_evidence

Fügt jedem Treffer Quelle, Confidence, Freshness, Provider und Policy-Status hinzu.
Ausgabe ist das finale `ContextItem`-Schema (siehe `codecompass-confidence-evidence.md`).

### [9] fit_context_budget

```python
@dataclass
class ContextBudget:
    max_tokens: int
    compress_first: bool = True
```

Reihenfolge:
1. Nochmalige Kompression aller noch nicht gekürzten Snippets.
2. Entfernung von Treffern mit niedrigstem Score, bis Budget eingehalten.
3. Jeder entfernte Treffer: `reason_code: "over_budget"`.

Kein blindes Abschneiden ohne Trace.

### [10] emit_trace

```python
@dataclass
class ContextTrace:
    query: str
    kept: list[ContextItem]
    dropped: list[DroppedCandidate]
    pipeline_stats: PipelineStats   # Treffer pro Stufe, Laufzeit, Budget-Ausschöpfung

@dataclass
class DroppedCandidate:
    raw: RawCandidate
    reason_code: Literal["denied_path", "duplicate", "stale", "low_score", "over_budget"]
    dropped_at_step: str
    detail: str | None
```

---

## Verworfene Treffer: reason_code-Übersicht

| reason_code   | Schritt               | Erklärung                                             |
|---------------|-----------------------|-------------------------------------------------------|
| `denied_path` | apply_policy_filter   | Pfad liegt in denied_paths oder Recht fehlt           |
| `duplicate`   | deduplicate           | Gleicher Treffer aus anderer Quelle, geringerer Score |
| `stale`       | rank_by_freshness     | History-Treffer älter als stale_days                  |
| `low_score`   | fit_context_budget    | Score zu niedrig nach Ranking                         |
| `over_budget` | fit_context_budget    | Token-Budget überschritten                            |

---

## Tests

| Test                              | Beschreibung                                                       |
|-----------------------------------|--------------------------------------------------------------------|
| `test_policy_filter_drops_denied` | denied_path → DroppedCandidate mit reason_code=denied_path        |
| `test_deduplicate_keeps_highest`  | Zwei Quellen für gleichen Pfad → nur höchster Score bleibt        |
| `test_budget_compression_first`   | Budget überschritten → zuerst Kompression, dann Removal           |
| `test_over_budget_trace`          | over_budget-Treffer erscheinen vollständig im ContextTrace        |
| `test_freshness_stale_penalized`  | Stale History-Treffer hat niedrigeren Gesamt-Score                |
| `test_full_pipeline_fixture`      | End-to-End mit Fixture-Projekt, alle 10 Schritte durchlaufen     |
