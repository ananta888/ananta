# CodeCompass Confidence und Evidence pro Kontexttreffer (COSMOS-014)

## Ziel

Jeder Kontexttreffer erklärt, warum er relevant ist und wie sicher diese Einschätzung
ist. Confidence ist kein LLM-Gefühl — sie wird deterministisch aus messbaren Signalen
abgeleitet.

---

## ContextItem Schema

Das `ContextItem` ist das zentrale Output-Objekt der Context Curation Pipeline.
Alle Felder nach `snippet` sind Pflicht:

```python
@dataclass
class ContextItem:
    # Basis-Identifikation
    item_id: str
    node_id: str | None           # Graph-Node-ID, falls vorhanden
    path: str                     # Dateipfad
    symbol: str | None            # Funktions-/Klassenname, falls bekannt
    snippet: str                  # Code-Ausschnitt (ggf. truncated)
    truncated: bool               # True wenn Snippet gekürzt wurde

    # Pflicht-Evidence-Felder (COSMOS-014)
    evidence: list[EvidenceItem]  # Quellen, die den Treffer belegen
    confidence: float             # 0.0–1.0, aus Signalen berechnet
    freshness: float              # 0.0–1.0, aus Alter und Aktivität
    provider: str                 # "codecompass" | "augment" | "manual" | "history"
    policy_status: str            # "allowed" | "denied" | "uncertain"
    reason: str                   # Warum dieser Treffer zur Query relevant ist

    # Ranking-Kontext (optional, für Debugging)
    score: float                  # finaler Gesamt-Score nach allen Ranking-Stufen
    active_status: CodeStatus     # aus Active-vs-Deprecated-Analyse
```

---

## EvidenceItem Schema

```python
@dataclass
class EvidenceItem:
    evidence_type: Literal[
        "ast_call",          # AST-Analyse: Funktion wird aufgerufen
        "ast_import",        # AST-Analyse: Modul wird importiert
        "ast_implements",    # AST-Analyse: Klasse implementiert Interface
        "test_reference",    # Testfall deckt diesen Pfad ab
        "doc_mention",       # Vorkommen in Dokumentation
        "config_reference",  # Config-Key oder ENV referenziert diesen Pfad
        "route_reference",   # API-Route zeigt auf diesen Code-Pfad
        "commit_history",    # Commit-History zeigt Aktivität
        "manual",            # Manuell eingetragene Evidence
    ]
    source_file: str | None       # Datei, in der die Evidence gefunden wurde
    line: int | None              # Zeile der Evidence
    confidence: float             # Confidence dieser einzelnen Evidenz-Quelle
    detail: str | None            # kurze Erklärung, z. B. "aufgerufen von hub.py:142"
```

---

## Confidence-Berechnung

Confidence ist **nicht** eine LLM-Schätzung. Sie wird aus den vorhandenen
EvidenceItems abgeleitet:

| Evidence-Typ         | Basis-Confidence | Begründung                                      |
|----------------------|------------------|-------------------------------------------------|
| `ast_call`           | 0.9              | Direkter AST-Nachweis, deterministisch          |
| `ast_import`         | 0.9              | Direkter AST-Nachweis                           |
| `ast_implements`     | 0.9              | Direkter AST-Nachweis                           |
| `route_reference`    | 0.85             | Strukturierter Nachweis aus Routing-Analyse     |
| `config_reference`   | 0.8              | Konfigurationsreferenz, meist eindeutig         |
| `test_reference`     | 0.75             | Test schlägt Brücke, aber kein direkter Call    |
| `commit_history`     | 0.65             | Historisch, nicht aktueller Code-Zustand        |
| `doc_mention`        | 0.5              | Dokumentation kann veraltet sein                |
| `manual`             | konfig.          | Explizit gesetzt, immer höchste Priorität       |
| LLM-Einschätzung     | 0.4              | Immer als niedrigste Quelle behandelt           |

Berechnung:

```python
def compute_confidence(evidence: list[EvidenceItem]) -> float:
    if not evidence:
        return 0.0
    # Höchste einzelne Confidence als Basis
    base = max(e.confidence for e in evidence)
    # Bonus für mehrere übereinstimmende Quellen (max +0.05)
    corroboration_bonus = min(0.05, (len(evidence) - 1) * 0.02)
    return min(1.0, base + corroboration_bonus)
```

LLM-Einschätzungen werden nie als primäre Quelle akzeptiert — sie können
Evidence ergänzen, aber nicht alleine einen `ContextItem` mit `confidence > 0.4` erzeugen.

---

## Unsichere Treffer

```python
UNCERTAIN_THRESHOLD = 0.3
```

Wenn `confidence < UNCERTAIN_THRESHOLD`:
- `policy_status` wird auf `"uncertain"` gesetzt (sofern nicht bereits `"denied"`).
- Treffer erscheint im Output mit `[UNSICHER]`-Markierung.
- Wird in der Ranking-Pipeline nicht entfernt, aber mit Score-Abzug versehen.

---

## Evidence-Zusammenfassung im Output

Antworten können eine kompakte Evidence-Liste ausgeben:

```
[Kontext] Gefunden in 3 Dateien, davon 2 via AST-Analyse (confidence: 0.90),
          1 via Dokumentation (confidence: 0.50).
          1 Treffer als [UNSICHER] markiert (confidence: 0.25).
```

Implementierung über `summarize_evidence(items: list[ContextItem]) -> str`.

---

## Policy-Status

| policy_status  | Bedeutung                                                         |
|----------------|-------------------------------------------------------------------|
| `"allowed"`    | Pfad liegt in allowed_paths, alle Checks bestanden               |
| `"denied"`     | Pfad liegt in denied_paths oder Rechte fehlen; Item nicht im Output |
| `"uncertain"`  | Pfad-Prüfung nicht eindeutig, oder confidence < threshold        |

Items mit `policy_status="denied"` verlassen die Pipeline nicht — sie erscheinen
nur im ContextTrace als DroppedCandidate.

---

## Tests

| Test                                  | Beschreibung                                                      |
|---------------------------------------|-------------------------------------------------------------------|
| `test_confidence_ast_single`          | Ein ast_call-Evidence → confidence = 0.9                         |
| `test_confidence_multi_corroboration` | Drei ast-Evidence → confidence > 0.9 (Bonus), max 1.0           |
| `test_confidence_llm_only`            | Nur LLM-Einschätzung → confidence = 0.4, status uncertain        |
| `test_uncertain_threshold`            | confidence < 0.3 → policy_status=uncertain, Output-Markierung    |
| `test_denied_not_in_output`           | denied Item nicht in kept-Liste des ContextBundle                |
| `test_evidence_summary_format`        | summarize_evidence gibt korrekte Zusammenfassung zurück          |
| `test_no_evidence_zero_confidence`    | Leere Evidence-Liste → confidence = 0.0                          |
