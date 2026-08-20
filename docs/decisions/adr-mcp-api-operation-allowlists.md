# ADR: Stable operation IDs and fail-closed MCP/API allowlists

Status: accepted (2026-08-20)

## Context

The Hub already owns authentication, platform exposure and domain authorization. The former MCP exposure policy was transport-wide: once MCP and an authentication source were enabled, every registered tool and resource was listed and directly dispatchable. REST routes had no additive operation identity for a gradual rollout.

## Decision

The Hub owns one immutable `OperationDescriptor` catalog. IDs use the namespaces `mcp.tool.*`, `mcp.resource.*` and `api.*`. Descriptors record the transport target, method where applicable, access and risk classes, lifecycle, owner and side-effect status. Duplicate IDs or targets, unknown classes and default-enabled write/admin descriptors fail during registration.

`OperationPolicyService` is a pure decision port. It receives only a descriptor, normalized policy and an authentication context. It has no Flask, repository, audit or dispatch dependency. Unknown operations and disabled lifecycle states always fail closed. For an enforced transport, lack of an explicit operation or versioned-group allow is deny; an operation or group deny wins over every allow.

Authentication remains the existing Hub authentication. The operation gate is an additional exposure decision after authentication and before domain logic. A positive operation decision never replaces argument validation, task/goal authorization, approval gates or any domain policy.

The first versioned groups are:

- `mcp.read.v1`: the explicit MCP read tool/resource baseline at this decision
- `mcp.write.v1`: the explicit mutating MCP baseline, not enabled by migration defaults
- `api.read.v1`: the first low-risk REST read rollout
- `api.admin.v1`: config update and policy rollback, not enabled by read-only profiles

Group membership is an explicit versioned tuple. Adding a registry operation does not silently add it to an existing group.

## Migration and compatibility

When `operation_policy` is absent, the migration adapter enforces only MCP tool/resource transports and allows `mcp.read.v1`. Existing global `exposure_policy.mcp` remains the transport/authentication authority. Mutating MCP operations require an explicit new policy grant. API transport gating remains disabled until `api` is included in `enforced_transports`; prioritized routes already carry operation metadata without URL, method or response changes.

## Consequences

The design preserves the Hub-worker boundary: policy resolution and routing stay in the Hub, and workers receive only already delegated work. It protects SRP by keeping catalog, pure decision, persistence/revision, observability and Flask adaptation separate. DIP is protected because the evaluator depends on `OperationRegistryPort`, not MCP dispatch or Flask globals.
