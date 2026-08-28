# Optional training-backend threat model

## Boundary

The Hub owns admission, queueing, tenant scope, policy, approval, promotion and
rollback. An optional backend is execution code in one isolated Worker
container. It cannot dispatch work, reach the Hub database, expose an upstream
WebUI or accept arbitrary trainer arguments.

## Threats and controls

| Threat | Fail-closed control |
| --- | --- |
| Config/command injection | Closed Ananta request, worker-owned compiler, fixed argv, no shell |
| Dynamic Python/recipe import | Forbidden config keys; torchtune recipe registry is code-owned and frozen |
| Remote model code / pickle | `trust_remote_code` forbidden; release accepts safetensors adapter output only |
| Dataset/model exfiltration | Internal Compose network, offline environment and deny-by-default egress policy |
| Telemetry/tracking leakage | Axolotl/HF tracking opt-outs and no external reporter configuration |
| Symlink/path traversal | Resolved admitted roots, symlink rejection and per-attempt artifact root |
| Artifact poisoning | Model/dataset/config/backend/version binding plus SHA-256 manifest admission |
| Checkpoint confusion | Exact backend, version, model, dataset, config and format comparison |
| Cancellation escape | One process group per attempt, TERM then bounded KILL, Hub fencing remains authoritative |
| Supply-chain drift | Exact top-level package pin, pip inventory, digest-pinned SBOM/vulnerability scanners and machine-decided fail-closed release gate |

No container uses privileged mode, Docker socket, host networking or a public
port. Model and dataset mounts remain read-only; only the attempt/state volume
and bounded tmpfs are writable.

AutoTrain Advanced and torchtune add maintenance risk because upstream has
ended active development. Their profiles remain experimental/default-off and
the release policy returns No-Go for production even if CPU contracts pass.
