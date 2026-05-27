# KRITIS Deterministic Workflow Target Model

## Purpose

Define deterministic expectations for critical hub-controlled workflows while keeping user-facing UX flows flexible where safe.

## Scope split

1. **Safety-critical workflows (strict deterministic)**
   - mutation approval and apply
   - high-risk execution gating
   - deterministic repair execution boundaries
   - escalation/review decision flows
2. **User-facing orchestration flows (controlled flexibility)**
   - convenience retries
   - non-critical read-model refreshes
   - advisory suggestions without state mutation

## Deterministic principles

- explicit named states per critical workflow
- explicit transition guards (policy, approval, preconditions)
- fail-visible invalid transitions (structured error, no silent fallback)
- auditable transition decisions and denials
- bounded timeout/recovery transitions

## Hub-worker alignment

- hub remains owner of workflow state decisions
- workers execute delegated steps only
- no worker-to-worker orchestration loops
- deterministic state changes must remain hub-observable

## Initial enforcement priority

1. mutation + approval workflow
2. repair execution workflow
3. high-risk execution policy workflow
4. review/escalation workflow
