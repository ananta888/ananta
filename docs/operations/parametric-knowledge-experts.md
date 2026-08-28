# Parametric knowledge expert operations

The feature is default-off. Keep `ANANTA_KNOWLEDGE_EXPERTS_ENABLED=false` and
the rollout stage `off` until every configured gate passes. RAG remains
enabled independently and is the mandatory fallback for unavailable runtimes,
insufficient resources and citation-required answers.

Activation and rollback are Hub commands. Operators must provide a reason and
the expected active generation; compare-and-swap rejects stale consoles. Never
copy a private signing key into a Worker. Workers receive public verification
keys and read-only, content-addressed safetensors artifacts.

For an incident, disable expert routing first, verify that RAG answers remain
healthy, revoke affected manifests, invalidate Worker residency, and retain
bounded audit reason codes. Do not log prompts, source text, secrets, adapter
contents or raw high-cardinality tenant/expert identifiers.

GA is forbidden while research reproduction, runtime capability, security,
benchmark or operations gates are blocked. A passing unit test or mock runtime
does not change those gate states.

The Hub stores rollout state in
`ANANTA_KNOWLEDGE_EXPERTS_ROLLOUT_STATE` (default
`data/knowledge-expert-rollout.sqlite3`). Admission requires explicit passing
research-reproduction, runtime-capability, security, benchmark and operations
signals. A failed or incomplete signal returns the scope to `off`; it never
opens an approval prompt or waits for an operator.

Each observation has an idempotency identifier and is bound to the current
`shadow`, `canary` or `ga` stage. Scope/security violations, conflicts,
hallucinations, OOM and cache errors stop the rollout immediately. Error rate,
mean quality delta and p95 latency are evaluated against the configured policy.
After the bounded shadow window the Hub atomically activates the candidate and
uses a stable scope/request hash for canary assignment. After the canary window
it promotes to GA. A stop during canary or GA automatically compare-and-swaps
back to the last-good generation. If that rollback CAS fails, the controller
records `knowledge_expert_automatic_rollback_failed` and continues to return a
non-result-affecting decision so RAG/base remains the serving path.

No rollout test or production rollout transition requires a human response.
Operators can still disable routing or investigate an incident, but lack of an
operator must never pause automatic completion, stop evaluation or fallback.
