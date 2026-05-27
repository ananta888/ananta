# Sandbox Operations Guide

**Track:** KRITIS-P3-SANDBOXING  
**Task:** K3-SBX-T10  
**Audience:** Operators, security engineers

## Overview

Ananta's execution sandbox enforces three isolation classes for command execution, filesystem access, and network egress. The sandbox is enforced at the `SandboxPolicyService` and `TerminalPolicyService` layers.

## Isolation Classes

| Class               | Allowed operations                                   | Typical use case                    |
|---------------------|------------------------------------------------------|-------------------------------------|
| `low-risk-readonly` | Read-only filesystem, no network, no privilege       | Code analysis, static inspection    |
| `bounded-mutable`   | Workspace-scoped writes, restricted network egress   | Standard task execution (default)   |
| `hardened-high-risk`| Docker/sudo/container ops, explicit admin approval   | Build pipelines, deployment tasks   |

The default isolation class is `bounded-mutable`. Change it in `worker_runtime.default_isolation_class` in the hub config.

## Filesystem Controls

- Only paths under `allowed_workspace_roots` (default: `/workspace`, `/project-workspaces`) are writable.
- `blocked_path_fragments` rejects any path containing `/.ssh`, `/etc/`, `/proc/`, `/sys/`.
- `enforce_workspace_boundary: true` is the default and must not be disabled in production.

## Network Egress Controls

- `egress_mode: restricted` (default) — only explicitly allowlisted domains/CIDRs.
- `egress_mode: open` — unrestricted outbound; use only in isolated lab environments.
- Configure `allowed_domains` and `allowed_cidrs` in the sandbox policy config.

## Terminal Access Policy

Terminal sessions are controlled by `TerminalPolicyService`:
- Admin users: can list, create, attach, read, write, and kill worker sessions; list hub sessions.
- Regular users: can list, create, attach, read, and write worker sessions only.
- Hub-as-worker targets are blocked by default (`blocked_target_types: ["hub_as_worker"]`).
- Write-like operations (create, attach, write) on admin-gated targets require `role: admin`.

## Running in Hardened Mode

1. Set `worker_runtime.default_isolation_class: hardened-high-risk` in hub config.
2. Confirm via **Admin-Diagnose** in the UI that the sandbox class shows `hardened-high-risk`.
3. Test with the regression suite: `pytest tests/test_sandbox_escape_regression.py -v`.
4. Review `docs/security/kritis-sandbox-isolation-classes.md` for the full class taxonomy.
5. Enable mutation gate approval (`propose_policy.require_approval: true`) before deploying.

## Tradeoffs and Limitations

- `hardened-high-risk` blocks commands requiring elevated privileges unless the agent explicitly requests this class.
- `low-risk-readonly` does not support any file writes; tasks that produce artifacts will fail unless the workspace root is excluded from the restriction.
- Sandbox policy enforcement is advisory for the hub's own process space — it applies to agent-issued commands, not to the hub's internal Flask process.
- Network CIDR matching is pattern-based string comparison; use a dedicated network-level firewall for cryptographic enforcement in critical environments.

## Known Compatibility Issues

- Some npm/pip install operations require network egress; add the relevant registry domains to `allowed_domains`.
- Git operations over SSH require the remote hostname in `allowed_domains` and SSH port 22 in `allowed_cidrs`.
- Docker build commands require `hardened-high-risk` class; builds will be rejected under `bounded-mutable`.

## Regression Test Coverage

Run: `pytest tests/test_sandbox_policy_service.py tests/test_sandbox_escape_regression.py -v`

The suite verifies:
- Default normalization produces safe defaults.
- `sudo`/`docker`/container commands require `hardened-high-risk` and are denied under `bounded-mutable`.
- Filesystem boundary violations are rejected.
- Network egress decisions respect `egress_mode` and allowlists.
- Hardened profiles cannot be silently weakened by config omissions.
