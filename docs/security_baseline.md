# Security baseline for goal workflows

This document captures a minimum security baseline for goal ingestion, planning, execution and artifact flows.

Principles

- Authentication: require authenticated requests for all goal and plan mutation endpoints.
- Authorization: per-resource capability checks (who may submit, edit plans, fetch artifacts).
- Least privilege: default API views return minimal fields; advanced fields require explicit scopes.
- Auditability: persist policy decisions, approvals and execution provenance for later review.
- Safe defaults: disable local self-execution by default; require explicit operator opt-in.

Recommendations

- Record who requested a goal, which policy version evaluated that request, and the resulting decision.
- Expose minimal artifact summaries to unauthenticated or low-privilege callers; full artifacts require a grant.
- Apply capability-scoped authorization checks to plan edits, artifact retrieval and override actions.

See docs/hub_fallback.md and docs/execution_scope.md for related operational controls.
