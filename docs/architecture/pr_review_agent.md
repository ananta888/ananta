# PR Review Agent Boundary (Review-Only First)

## Status and gating

PR review automation is intentionally **gated** behind KRITIS readiness tasks:

- `todo.kritis.json:K1-AUD-T15`
- `todo.kritis.json:K1-MUT-T13`
- `todo.kritis.json:K1-EVO-T09`

Until these gates are stable, the PR review path stays in test/dogfooding mode and remains strictly review-only.

## Architectural position

- The **Hub** owns orchestration and queue decisions.
- Webhook ingestion only creates a review task in the queue.
- Workers execute delegated analysis work.
- No worker-to-worker orchestration is introduced.

This keeps the existing hub-worker contract intact and auditable.

## Review-only behavior

The initial PR review flow supports:

1. Receive a signed provider webhook (GitHub/GitLab style).
2. Validate provider, repository, event policy, and signature.
3. Create one queue task for review analysis.
4. Run allowed checks and diff analysis in delegated execution.
5. Produce a `ReviewArtifact` summary.

## Explicit non-goals in this phase

- No auto-merge.
- No direct push/commit from review ingestion.
- No hidden execution inside request handlers.
- Comment posting remains a separate outbound adapter with policy and dry-run support.
