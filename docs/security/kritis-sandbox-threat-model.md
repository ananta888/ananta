# KRITIS Sandbox Threat Model (K3-SBX-T01)

## Scope

Threat model for execution and test runtime environments with separation between:

- **Dev convenience mode** (local velocity first)
- **KRITIS mode** (risk reduction and bounded blast radius first)

## Primary assets

- host filesystem and credentials
- hub control-plane integrity
- worker runtime integrity
- audit trail integrity
- tenant/task data and artifacts

## Threat categories

1. **Filesystem abuse**
   - unauthorized read/write outside bounded workspace
   - symlink traversal and path confusion
   - secret discovery via broad filesystem access
2. **Network abuse**
   - uncontrolled egress to untrusted endpoints
   - lateral movement across internal services
   - covert exfiltration through unrestricted outbound channels
3. **Privilege escalation**
   - container process obtains elevated Linux capabilities
   - root execution inside runtime leads to host-impacting behavior
4. **Container breakout**
   - runtime exploit escapes namespace/cgroup constraints
   - host kernel or daemon interface abuse
5. **Orchestration bypass**
   - worker path performs mutation outside hub-approved flow
   - non-audited transition/fallback paths in critical workflows

## KRITIS-mode security objectives

- least privilege runtime by default
- deny-by-default on high-risk mutation without explicit approval
- explicit and auditable state transitions for critical flows
- bounded recovery and timeout handling (no unbounded loops)
- reproducible execution envelopes for incident reconstruction

## Control mapping

| Threat | Primary control |
| --- | --- |
| Filesystem abuse | bounded workspace mount, read-only rootfs for hardened classes |
| Network abuse | restricted egress class + explicit allowlist |
| Privilege escalation | rootless runtime, dropped capabilities, no new privileges |
| Container breakout | hardened runtime profile + seccomp/apparmor baseline |
| Orchestration bypass | hub-owned transition guards + execution audit events |

## Residual risk statement

Dev mode may permit broader capabilities for productivity. KRITIS deployments must select hardened isolation classes and enforce policy/audit controls for all critical execution paths.

## Autonomous-agent extensions

The following threats are distinct and may compound each other:

| Origin | Threat examples | Required observation |
| --- | --- | --- |
| Model behavior | goal fixation, shortcutting, reward hacking, delayed or false self-report | model/profile, seed, tool request and separate self-report event |
| Policy configuration | prevention disabled outside an authorized local evaluation, overly broad tool or target scope | immutable policy revision and admission decision |
| Technical boundary | path traversal, live egress after deny, process escape, privilege increase | external filesystem/network/process/privilege detector event |
| Credential boundary | reuse outside the bound run or use after revocation | lease ID, run binding and revocation receipt without secret material |
| Orchestration boundary | Worker creates follow-up work or stops another Worker directly | Hub assignment and Hub-issued control-operation receipts |

A reported success is not proof of a technical escape. Conversely, absence of
an Agent self-report is not proof that containment held. `agent_self_report`
and external detector sources therefore remain separate in the immutable event
ledger.

### Post-boundary behavior

After a confirmed crossing the affected execution is immediately marked
`execution_allowed=false`. The Hub requests sandbox freeze or termination,
egress fencing and run-credential revocation through separate ports. Missing
or negative receipts produce `containment_failed_closed`; they never reopen the
run. Forensic retention is bounded and cleanup is a later, independent action.

### Authorized evaluation boundary

Automated adversarial evaluation is permitted only for explicit `local:*`
targets in a revisioned policy. Disabling preventive prompting or behavior
training does not disable telemetry, the external stop path, egress fencing or
credential revocation. No external service becomes an attack target by being
reachable from a test environment.
