# Planning Context Compactor

## Zweck
Additiver Hub-seitiger Compactor vor dem Propose-/Planning-LLM-Call. Ziel ist, große Goal/Context/Mode-Inputs deterministisch und optional LLM-gestützt zu verdichten, ohne kritische Constraints zu verlieren.

## Ablauf
1. Deterministic pre-trim
2. Optionaler LLM-Compaction-Call (Strict JSON)
3. Schema-Validierung
4. Constraint-Guardrails
5. Fallback-Kette (Retry -> deterministic-only -> optional bypass)

## Policy-Felder
- `context_compaction_enabled` (default `true`)
- `context_compaction_required` (default `false`)
- `context_compactor_timeout_seconds` (30..120)
- `context_compactor_max_output_chars` (1000..50000)
- `context_compactor_retry_attempts` (0..3)
- `context_compactor_fail_open` (default `false`)
- `context_compactor_profile` (`default`, `lmstudio_laptop`, `ollama_rtx3080`)
- `context_compactor_preserve_keywords` (z. B. `security`, `policy`, `verification`, `review`, `constraints`)

## Profile
- `lmstudio_laptop`: konservativere Limits
- `ollama_rtx3080`: großzügigere Limits

## Fehlerklassen
- `context_compactor_timeout`
- `context_compactor_unparseable_output`
- `context_compactor_schema_violation`
- `context_compactor_constraint_loss_detected`
- `context_compactor_contract_constraint_loss`
- `context_compactor_runtime_unavailable`

## Betrieb / Rollout
- Default ist fail-closed (`context_compactor_fail_open=false`)
- Für Rollout kann per Policy `context_compaction_enabled=false` genutzt werden.
- Telemetry enthält nur Meta (keine Roh-Prompts/Context-Dumps).
