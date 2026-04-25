# ADR: Icebox for Heavy Client Features (LSP, Flow Builder, Large Eclipse Refactors)

- **Status:** Accepted (Icebox)
- **Date:** 2026-04-25
- **Scope:** OSS golden-path releases

## Context

The current platform focus is stable hub-controlled orchestration, installability, controlled integrations, and auditable execution paths. Heavy client-side initiatives (full LSP server work, visual flow builder, and large Eclipse refactors) can consume significant capacity and risk architecture drift if started too early.

## Decision

The following items remain in the icebox:

1. Full LSP server implementation/rewrite.
2. Visual flow builder as a major product surface.
3. Large Eclipse refactors beyond adapter/runtime bootstrap boundaries.

These items are deferred and are not part of active release scope.

## Why

- Preserves thin-client direction and avoids Medusa-style complexity.
- Avoids duplicate orchestration behavior outside the hub.
- Reduces regression risk while core OSS paths are still hardening.

## Promotion conditions

An icebox item may enter active scope only when all conditions are met:

1. Hub-worker invariants and mutation/approval gates are stable in release evidence.
2. PR review and release gates run reliably without manual rescue loops.
3. A bounded architecture proposal exists (clear interfaces, no orchestration duplication).
4. The proposal includes migration strategy, rollback strategy, and measurable acceptance tests.

## Consequences

- Current release tracks stay focused and reviewable.
- Large UI/editor initiatives are explicit future scope, not accidental drift.
