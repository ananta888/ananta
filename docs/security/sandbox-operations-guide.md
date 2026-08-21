# Sandbox Operations Guide

**Track:** KRITIS-P3-SANDBOXING  
**Task:** K3-SBX-T10  
**Audience:** Operators, security engineers

## Overview

Ananta currently classifies three isolation classes and applies them at selected
Hub admission and terminal-policy boundaries. `SandboxPolicyService` evaluates
command classes, while `TerminalPolicyService` and the managed SSH wrapper
enforce their own terminal decisions. These policy checks are not an OS- or
container-level sandbox by themselves.

`agent/services/sandbox_backend.py` currently defines a port and an in-memory
test fake; it does not provide a production sandbox backend. A deployment may
claim technical containment only when a concrete backend and its filesystem,
network, process, namespace and resource controls have separate runtime
evidence.

## Isolation Classes

| Class               | Allowed operations                                   | Typical use case                    |
|---------------------|------------------------------------------------------|-------------------------------------|
| `low-risk-readonly` | Read-only filesystem, no network, no privilege       | Code analysis, static inspection    |
| `bounded-mutable`   | Workspace-scoped writes, restricted network egress   | Standard task execution (default)   |
| `hardened-high-risk`| Docker/sudo/container ops, explicit admin approval   | Build pipelines, deployment tasks   |

The default isolation class is `bounded-mutable`. Change it in `worker_runtime.default_isolation_class` in the hub config.

## Filesystem Policy Inputs

- `allowed_workspace_roots` defaults to `/workspace` and `/project-workspaces`.
- `blocked_path_fragments` defaults to `/.ssh`, `/etc/`, `/proc/`, `/sys/`.
- `enforce_workspace_boundary: true` is the normalized policy default.
- The managed SSH wrapper performs its own path containment checks. The generic
  policy object alone does not make arbitrary process filesystem access safe.

## Network Egress Policy Inputs

- `egress_mode: restricted` is the normalized default.
- `egress_mode: open` expresses an operator policy request and must be used only
  in isolated lab environments.
- Configure `allowed_domains` and `allowed_cidrs` in the sandbox policy config.
- A production containment claim additionally requires enforcement at the
  concrete network namespace, proxy or firewall boundary. String normalization
  is not egress enforcement.

## Terminal Access Policy

Terminal sessions are controlled by `TerminalPolicyService`:
- Admin users: can list, create, attach, read, write, and kill worker sessions; list hub sessions.
- Regular users: can list, create, attach, read, and write worker sessions only.
- Hub-as-worker targets are blocked by default (`blocked_target_types: ["hub_as_worker"]`).
- Write-like operations (create, attach, write) on admin-gated targets require `role: admin`.

## Running in Hardened Mode

1. Set `worker_runtime.default_isolation_class: hardened-high-risk` in hub config.
2. Confirm via **Admin-Diagnose** that admission reports `hardened-high-risk`.
3. Test with the regression suite: `pytest tests/test_sandbox_escape_regression.py -v`.
4. Review `docs/security/kritis-sandbox-isolation-classes.md` for the full class taxonomy.
5. Enable mutation gate approval (`propose_policy.require_approval: true`) before deploying.
6. Independently verify the concrete container/process/filesystem/network
   backend. Selecting the class does not create those boundaries.

## Tradeoffs and Limitations

- `hardened-high-risk` blocks commands requiring elevated privileges unless the agent explicitly requests this class.
- `low-risk-readonly` does not support any file writes; tasks that produce artifacts will fail unless the workspace root is excluded from the restriction.
- Sandbox class policy is an admission control for covered call paths, not a
  general containment boundary for the Hub process or every Worker subprocess.
- Network CIDR matching is pattern-based string comparison; use a dedicated network-level firewall for cryptographic enforcement in critical environments.

## Known Compatibility Issues

- Some npm/pip install operations require network egress; add the relevant registry domains to `allowed_domains`.
- Git operations over SSH require the remote hostname in `allowed_domains` and SSH port 22 in `allowed_cidrs`.
- Docker build commands require `hardened-high-risk` class; builds will be rejected under `bounded-mutable`.

## Regression Test Coverage

Run: `pytest tests/test_sandbox_policy_service.py tests/test_sandbox_escape_regression.py -v`

The suite verifies policy normalization and admission decisions:
- Default normalization produces safe defaults.
- `sudo`/`docker`/container commands require `hardened-high-risk` and are denied under `bounded-mutable`.
- Filesystem boundary violations are rejected.
- Network egress decisions respect `egress_mode` and allowlists.
- Hardened profiles cannot be silently weakened by config omissions.

It does not prove namespace, syscall, mount, network, credential or process
containment. Those properties require backend-specific integration and runtime
gates.
