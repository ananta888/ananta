# Text-Quality: Datenschutz und Löschpfad

Dieses Dokument ergänzt `docs/text-quality-anti-slop.md` und beschreibt
verbindlich, wie Ananta mit Texteingaben für die Textqualitäts-
Subsysteme umgeht. Ananta misst Qualität; Ananta beweist
**keine** Autorschaft und erkennt **keine** Mensch-Maschine-Zuordnung
verlässlich.

## Was reingeht

- Direkte API-Eingaben (`/api/text-quality/evaluate`,
  `/api/text-quality/criteria/extract`) **und** Plan-Task-Texte aus
  `PlanningEvaluationService`, wenn `text_quality.enabled` und
  `evaluate_planning_outputs=true`.
- Beispiel-Texte für die Criteria-Extraktion: max. 5 Beispiele,
  je max. 4000 Zeichen (Summe innerhalb `max_input_chars`).

## Was redigiert wird (VOR LLM/Sandbox)

`_redact` in `criteria_extractor_service.py` ersetzt im Input-Prompt
**vor** jeder LLM- oder Sandbox-Ausführung:

- E-Mail-Adressen → `[REDACTED_EMAIL]`.
- `(api_key|token|password) = …` → `[REDACTED]` (regex ist case-insensitive).

Die Redaction gilt für Prompt **und** gespeicherte `source_refs.review_note`.

## Was persistiert wird

| Feld                            | Inhalt                                                 | Beispiel                  |
|---------------------------------|--------------------------------------------------------|---------------------------|
| `TextQualityEvaluationDB.identity_checksum` | `sha256(run_id + text_hash + criteria_version + evaluator_version + content_kind)` | dedupliziert |
| `result_payload`                | Scores, Reason-Codes, Versions, `grounding_status`     | **kein Rohtext**          |
| `TextQualityCriteriaSetDB.criteria_payload` | bounded Phrasen + Trait-Liste                        | **kein Volltext**         |
| `source_refs`                   | `sha256(preview)` + bounded Preview + Reviewer-Note     | ≤ 120 Zeichen preview     |
| `Finding.excerpt`               | bounded, max. 160 Zeichen                              | truncated                |

## Was **niemals** persistiert wird

- E-Mail-Adressen, Tokens, Passwörter (siehe Redaction).
- Volltexte der Eingaben oder Beispiele.
- Provider-Sitzungsschlüssel, Auth-Header, Host-Pfade.
- Beliebige Hostpfade aus Upstream-Bundle (siehe ADR).

## Audit-Aktionen

`agent/common/audit.log_audit` wird mit
`actor`, `evaluation_id`/`criteria_id`, `versions`, `checksum`
aufgerufen — niemals mit Volltext.

Liste (Auszug):

- `text_quality_evaluated`
- `criteria_extracted`
- `criteria_activated`
- `criteria_rejected`
- `criteria_archived`
- `provider_degraded`

## Source-grounding

Reason-Codes aus dem Evaluator, die eine Quelle voraussetzen
(`source_unverified`, `unsupported_specific_claim`), leiten sich aus
der Schnittmenge `mentioned_ids = SRC_*|RUN_*` und
`evidence_ids` ab. **Nur** in der Eingabe gelieferte Evidence-IDs
zählen. **Unbekannte** IDs werden `unverified`, nicht erfunden.

Negativbeispiel:

```text
Der Wert SRC_FORECASTED_42 belegt, dass unsere API 17 Prozent schneller ist.
```

Wenn `evidence_refs = []`, dann:

- `mentioned_ids = {SRC_FORECASTED_42}`,
- `evidence_ids = ∅`,
- `unknown_ids = {SRC_FORECASTED_42}`,
- `grounding_status = "unverified"`,
- Reason-Codes: `source_unverified`, `unsupported_specific_claim`.
- Slop-Score wird zusätzlich um 0.15 erhöht.

## Retention

Persistierte Evaluationen und CriteriaSets folgen der allgemeinen
`AGENT_CONFIG`-Retention. Volltextfreiheit macht eine
datenschutzfreundliche Vor-Aggregation möglich — `text_quality_comparable`
gruppiert ohne Klartext-Vergleich.

## Löschpfad

1. Stoppen Sie den Learning-Loop, damit keine neuen Aggregationen
   entstehen.
2. Setzen Sie `text_quality.enabled=false` und
   `evaluate_planning_outputs=false` (Hot-Reload nach Restart).
3. Löschen Sie `TextQualityEvaluationDB` und `TextQualityCriteriaSetDB`
   via Admin-Tool oder direkten SQL-Befehl. **Hinweis**: Die Tabellen
   enthalten bewusst **keinen Volltext**.
4. Audit-Logs (`criteria_activated`, `provider_degraded`, …) bleiben
   gemäß Audit-Retention für die gesetzlich vorgeschriebene Frist.

## Was Ananta **nicht** verspricht

- Erkennung von menschlicher vs. KI-Autorschaft.
- Optimierung auf einen `HUMAN_ONLY`- oder `AI_ONLY`-Klassifikationswert.
- Edit-in-place auf Dateien (siehe `core_decisions`).
- Beliebige Schreibregeln aus einem LLM ohne Allowlist.
