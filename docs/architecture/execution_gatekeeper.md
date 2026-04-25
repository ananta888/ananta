# Single Execution Gatekeeper (OSS Core)

## Purpose

All execution-like actions must pass one shared gatekeeper sequence owned by the Hub.

The gatekeeper exists to prevent policy bypass, approval spoofing and direct client-to-worker execution shortcuts.

## Required gatekeeper sequence

Every execution-like path must run these steps in order:

1. Validate explicit context and target identifiers.
2. Resolve capability and execution scope.
3. Evaluate policy decision (allow / deny / approval_required / degraded).
4. Check approval binding (action, capability, scope/context hash, freshness where required).
5. Emit pre-execution audit/trace event.
6. Execute bounded action (tool, worker step, domain action, repair).
7. Emit post-execution result audit/trace event.
8. Persist artifact/result metadata and references.

## Execution-like path classification

The same gatekeeper sequence applies to:

- normal task execution
- retry execution attempts
- repair execution paths
- tool execution
- worker execution
- domain action execution

No path is allowed to “skip” policy/approval because it is marked as repair, retry, fallback, or internal.

## Forbidden shortcuts

- No direct client-to-worker execution channel.
- No trusted bypass that jumps directly to shell/tool execution after policy deny.
- No synthetic “approved=true” client flag without bound approval reference and scope.
- No repair/retry special-case that bypasses audit append events.

## SOLID alignment

- **SRP:** gatekeeping logic remains a single responsibility rather than spread over ad-hoc call sites.
- **DIP:** execution call sites depend on a policy/approval contract, not on direct trust of callers.
