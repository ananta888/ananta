# KRITIS Hardened Container Profile (K3-SBX-T03)

## Profile ID

`kritis-hardened-v1`

## Intended runtime modes

- `sandbox`
- `strict`

## Baseline controls

- rootless runtime required
- read-only root filesystem
- drop all Linux capabilities
- `allowPrivilegeEscalation=false`
- seccomp profile `runtime/default`
- apparmor profile `docker-default`
- restricted egress network class
- bounded writable workspace mount (`bounded_rw`)
- explicit tmpfs mounts for ephemeral paths (`/tmp`, `/run`)

## Deployment profile integration

`agent/cli/deployment_profile_writer.py` now emits `container_hardening_profile` in generated deployment profile payloads:

- `default-dev-v1` for non-hardened local-dev style modes
- `kritis-hardened-v1` for sandbox/strict modes

This keeps hardening intent explicit, machine-readable, and auditable in deployment artifacts.
