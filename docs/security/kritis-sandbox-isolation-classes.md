# KRITIS Execution Isolation Classes (K3-SBX-T02)

## Goal

Define enforceable isolation classes that map task/tool risk to runtime boundaries.

## Class model

| Class | Typical use | Filesystem | Network | Privilege |
| --- | --- | --- | --- | --- |
| `low-risk-readonly` | read-only analysis, indexing, diagnostics | read-only rootfs, bounded read mounts | default-deny, explicit allowlist | non-root, no privilege escalation |
| `bounded-mutable` | controlled code edits/tests in workspace | bounded RW workspace, read-only rootfs elsewhere | restricted egress | non-root, reduced capability set |
| `hardened-high-risk` | mutation-capable, security-sensitive execution | read-only rootfs + explicit tmpfs + minimal RW workspace | restricted egress with strict policy | rootless, drop-all capabilities, no new privileges |

## Mapping guidance

- `task_kind=analysis` → `low-risk-readonly`
- `task_kind=coding` with bounded patch/test flow → `bounded-mutable`
- high-risk mutation / privileged operations / sensitive data handling → `hardened-high-risk`

## Enforcement requirements

1. selection is hub-controlled and policy-derived
2. effective isolation class is included in execution metadata/audit
3. fallback to a weaker class is disallowed without explicit policy exception
4. transition to mutation-capable actions requires class-compatible approval policy

## Audit requirements

Each execution must emit:

- selected isolation class
- decision source (policy/risk/approval)
- deny reason when class is insufficient
- trace/task linkage for reconstruction
