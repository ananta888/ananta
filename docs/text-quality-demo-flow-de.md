# Text-Quality-Demo-Flow (deutsch)

End-to-End-Beispiel, wie ein negatives Beispiel bis zu einem Prompt-Kandidaten
führt. Jeder Schritt hat eigene Review-Gates; **kein Schritt greift
automatisch** in den nächsten über.

## 1. Negativbeispiel landet im Review

Ein Redakteur markiert einen bestehenden Text als "wirkt floskelhaft" und
hinterlegt ihn in einem Issue. Der Text bleibt unter dem Beispiel-Budget
(max. 4000 Zeichen pro Beispiel, max. 5 Beispiele pro Extraktion).

## 2. Signale: Core + optionale Upstream-Detectoren

Der deterministische Core-Scanner (`agent.services.text_quality.deterministic_scanner`)
bewertet offline die deutsche Prosa gegen `profiles/de.json`.

Wenn `text_quality.external_detectors.avoid_ai_writing.enabled` gesetzt ist,
liefert der Sandbox-Provider (`scripts/avoid_ai_writing_detector_bridge.mjs`)
zusätzlich einen 0-100-Score. Beide Signale werden im `DetectorSignal`
gespeichert, **niemals** als finaler Slop-Score.

Optional kann `text_quality.llm_judge_enabled=true` einen LLM-Judge
aktivieren, der Reason-Codes und bounded improvement_hints liefert. LLM-Judge
nutzt `temperature=0`, JSON-Schema, bounded Input, maximal ein Repair.

## 3. Fusion + Evaluation

`TextQualityEvaluatorService.evaluate` ruft eine
**versionierte** `ScoreFusionPolicy` (`fusion-v1`), die:
- fehlende/degraded Provider aus dem Nenner entfernt und im `source_breakdown`
  markiert — **niemals** als Score 0.
- externe Scores getrennt vom Slop-Score führt.

Resultat wird in `TextQualityEvaluationDB` persistiert:
- `evaluation_id`, `criteria_set_id`, `prompt_version_id` als Verweise.
- `result_payload` enthält keinen Rohtext, sondern Scores, Reason-Codes,
  `grounding_status`, `evaluator_version`, `criteria_version`.

## 4. proposed CriteriaSet

`CriteriaExtractorService.extract` ruft das LLM mit JSON-Schema und
redigierten Beispielen (`_redact`: E-Mail, `api_key=…`, `token=…`) auf.
Nur Reason-Codes aus `KNOWN_REASON_CODES` werden akzeptiert. Der Output ist
**immer** `status="proposed"` und `requires_review=True` (bei
`confidence < 0.7`).

## 5. Review

`CriteriaReviewService.activate / reject / archive` verlangt
**actor** und **source**, sonst `ValueError("review_provenance_required")`.
Die Aktivierung schaltet eine vorherige aktive Version derselben
`profile_name / language / content_kinds`-Kombination atomar auf
`archived` und persistiert mit versioniertem
`canonical_checksum` (Deduplikation).

## 6. Persistente Evaluation

Eine `TextQualityEvaluationDB` wird **idempotent** über
`identity_checksum = sha256(run_id + text-hash + criteria_version + evaluator_version + kind)`
gespeichert. Wiederholte Evaluationen sind No-Ops.

## 7. Evolver-Trigger (nur bei completed + passender Version)

`PlanningPromptEvolverService._should_evolve` liest
`run.mode_data["__text_quality__"]`. **Nur** wenn
- `status == "completed"`
- `slop_score > max_slop_score` **oder** `depth_score < min_depth_score`

werden Reason-Codes zu **allowlisted** Prompt-Regeln aus
`prompt_rule_mapping.py` gemappt. Andere Texte triggern nicht.

## 8. Kandidat

`evolve_from_run` baut eine `PlanningPromptVersionDB` mit
`enabled=False` (default), erzeugt eine `PlanningReviewItemDB` mit
`reason_codes=["proposed", "requires_review"]`. **Kein Auto-Enable**.

## 9. Canary → Promotion/Rollback

`PlanningLearningLoopService._maybe_promote_canary` verlangt:
- technische Schwelle (`candidate_activation_threshold`)
- `text_quality_comparable=True` (gleiche `criteria_version`/`evaluator_version`)
- `text_quality_completed_count >= min_text_quality_runs`
- **zusätzlich** Textqualitäts-Nichtverschlechterung gegen eine
  versionsgleiche Baseline; Delta und Stichprobe landen im
  Candidate-Payload.

Rollback feuert bei signifikanter Verschlechterung
nach Mindestfenster (`rollback_min_runs`). Provider-Ausfall oder
Criteria-Version-Wechsel allein löst **keinen** Rollback aus.

## 10. Löschen / Retention

Texte werden als SHA-256 + bounded Preview referenziert. Audit-Aktionen
(`text_quality_evaluated`, `criteria_extracted`, `criteria_activated`,
`criteria_rejected`, `provider_degraded`) enthalten IDs/Versionen,
**niemals** Volltext oder Secret. Retention/Löschpfad siehe
`docs/text-quality-privacy.md`.
