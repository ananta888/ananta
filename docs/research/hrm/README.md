# HRM experiment research decision pack

Status: 32/32 research decisions completed; Hub grounding and promotion verified.

This pack resolves the research questions formerly tracked in
`todos/todo.hrm-experiment-reasoning-workbench.json`, now archived below
`todos/archiv/`. It does not install, train or execute HRM. `RUN_0001` proves
the assignment-bound research execution and promotion only; runtime, GPU,
containment, performance and accuracy remain unverified.

## Immutable evidence baseline

The official repository is bound to commit
`ac15626f8db096a63c775b84c9dc868776a6feda`, tree
`a3d5454a2ff1d438781b3146054b00ef663579d1`, and archive SHA-256
`f1b4750d77c4648ec5cd238adaca38e4d6397c5b4b6382b5132225f3033c49a3`.
The repository license is Apache-2.0. The selected paper is
`arXiv:2506.21734v3`, revised 2025-08-04, PDF SHA-256
`81b05b03ebd92748b1bc0e59d4b6e0d8271e1e56b84d1d589a03430db79b6e25`,
licensed CC-BY-4.0.

The exact file and gitlink digests are in `source-manifest.v1.json`. External
payloads are not committed. Hub catalog `catalog-6c38177316f67dfd` binds the
pre-promotion decision pack at repository revision `ddd471a4dc8ce63da4c4308e927b6a524985a93ca97f3aa5ce9ee2b11b8975dd`
as `SRC_0003`; the exact catalog, assignment, run and promotion digests are
recorded in the manifest.

## Material upstream findings

- `requirements.txt` names eleven packages without versions or hashes.
- `evaluate.py` loads checkpoints with `torch.load`; this is incompatible
  with the fail-closed tensor-only import policy.
- `pretrain.py` imports and writes to W&B directly.
- Sudoku and Maze builders download through `huggingface_hub`.
- ARC sources are gitlinks and therefore need their own immutable source and
  license bindings.
- The README describes CUDA 12.6, FlashAttention variants and example
  runtimes, but these remain documentation claims rather than Ananta runtime
  evidence.

Consequently no live CPU, Ampere or Hopper compatibility row is allowed. A
future worker image needs a complete lock, digest-pinned base and wheels,
offline build, SBOM, signature, provenance attestation, vulnerability snapshot,
license scan, isolation probe and hardware-specific run evidence.

## Architecture decision

HRM is a default-disabled experiment domain, not a chat provider and not a
LoRA backend. The Hub remains the only owner of admission, queue, routing,
policy, approval, leases, fencing, status projection and result acceptance.
An isolated worker may execute only one materialized assignment and owns only
its process group, local capability probe, bounded observations and cleanup.

The design protects SRP and DIP by keeping policy and authoritative state in
Hub services, transport behind a small `HrmWorkerPort`, puzzle behavior behind
codec/normalizer/validator ports and storage behind existing secure artifact
ports. It protects LSP and ISP by rejecting substitution of HRM semantics into
the LoRA-specific `TrainingBackend`, instruction dataset or PEFT registry.
OCP is protected through a new namespace and compatibility adapters instead of
renaming existing contracts.

## Fit and gap summary

| Layer | Decision | Boundary |
|---|---|---|
| Task, WorkerJob, slot lease, result capability, outbox | REUSE | Hub authority remains unchanged |
| Capacity, attempt, event and reconciliation mechanics | EXTEND | Extract mechanics only, not LoRA fields |
| HRM worker contract and domain run model | NEW | Transport independent and closed |
| Blob, archive, hashing, preview and split primitives | EXTEND | Consume through narrow security ports |
| Puzzle dataset, validator and visualization projection | NEW | No dynamic plugin loading |
| HRM checkpoint registry | NEW | PEFT adapter registry remains unchanged |
| Evaluation comparison and offline report rendering | EXTEND | Add task-specific validators and provenance |
| Hub API/SSE, Angular, Visual Process, TUI and CLI | EXTEND | Same Hub IDs and read model |
| HRM through ChatProvider or LoRA TrainingBackend | REJECT | Semantics are not substitutable |

## Security decisions

The authoritative threat register is `threat-model.v1.json`. Critical or high
risks have an owner, deny/mitigate/conditional disposition and a named future
gate. Unknown or ownerless risks block promotion.

Checkpoint admission is safetensors-only until another bounded tensor format
is separately approved. Digest, byte size, header, schema, shape and dtype are
checked before data is made visible. Pickle, `torch.load`, custom classes,
object hooks, dynamic imports and `trust_remote_code` are denied. Legacy
conversion is denied until a disposable, secret-free and networkless sandbox
has its own run evidence.

Network and telemetry are default-deny. Redirects, DNS answers and connect IPs
must be revalidated, environment proxies are disabled, streams and retries are
bounded, and partial data remains quarantined. W&B and remote LLM baselines
require explicit run-bound grants; secrets are handles rather than manifest,
argument or environment values.

The worker profile requires non-root UID/GID, no-new-privileges, cap-drop-all,
read-only root, tmpfs scratch, mount allowlists, no Docker socket, no host
PID/IPC/network, cgroup limits, seccomp and MAC enforcement. Missing mandatory
isolation yields a stable deny rather than best effort.

## Contracts and profiles

`schemas/hrm-experiments/contracts.v1.json` contains closed schemas for:

- capability and preflight
- puzzle dataset and checkpoint manifests
- run request and status
- monotonic event pages and integrity-bound cursors
- cancel request
- terminal result and artifacts
- task-specific evaluation report

Every security-bound object rejects unknown fields. Requests bind task,
assignment, WorkerJob, dispatch lease, attempt, epoch, deadline, policy/schema/
payload digests, tenant/project scope and effective limits. They contain no
worker URL, server path or secret.

`feasibility-profiles.v1.json` separates contract, mock, CPU, bounded Sudoku,
small NVIDIA, Maze, ARC, multi-GPU and remote-provider profiles. Only the
deterministic contract gate is currently eligible for local execution. Complex
profiles remain deferred or pending explicit approval and `RUN_*` evidence.

The Sudoku contract fixture is a bounded 9x9 integer grid created in Ananta.
Its validator requires shape/range validity, preservation of givens, valid
rows/columns/boxes and exact equality to the canonical solution. It provides no
model accuracy claim.

Maze and ARC are separate future plugins. Maze must define start, goal,
obstacles, path validity, shortest-path semantics and unsolvable cases. ARC
must define bounded grids/colors, train/test structure, output validation,
license and egress suitability. Neither blocks Sudoku.

## API and clients

`docs/contracts/hrm-experiments.openapi.yaml` defines only Hub endpoints under
`/api/hrm-experiments`. It covers capabilities, preflight, datasets, runs,
events, cancel, checkpoints, evaluations and reports with stable pagination,
sorting, idempotency and error codes. Feature and policy checks occur at
admission, attempt creation, dispatch, mutation and event access.

Angular extends the existing model-training feature with an explicit
experimental label and HRM-specific projections. Visual Process nodes store
only Hub dataset/run/checkpoint/evaluation IDs; canvas state has no runtime
authority. TUI and CLI call the same API, keep lists bounded, sanitize control
and ANSI content, and never implement queue, process, checkpoint or path logic.

## Candidate implementation slices

1. Shared Hub control-plane seams and HRM domain records.
2. Pinned and isolated worker runtime plus `HrmWorkerPort`.
3. Bounded Sudoku dataset/run/checkpoint/evaluation vertical slice.
4. Independent Maze and ARC plugins after source/license approval.
5. Checkpoint portability, evaluation, LLM baseline and comparison reports.
6. Hub API, Angular/Visual Process, TUI and CLI surfaces.
7. Adversarial hardening, complex evidence gates and experimental release.

This pack is passive. It creates no Track, internal Task, queue entry, grant,
worker address, tool, capability or budget. Only the Hub may derive a Track
from the exact promoted Category revision.

## Completion boundary

All 32 item decisions are complete. `HRMR-PLAN-003` closed when the Hub
validated and explicitly promoted Category revision
`pcat-ca9b68c8fbc9e84ca3a7b1e2` with `SRC_0003`, `RUN_0001`, approval
`30650f79-3389-439a-9b01-28dd42ca0ac9` and promotion receipt
`98939c6e-ab88-4de1-b405-77a6a66204d4`. This completes the research-decision
phase only. Live runtime and complex release gates remain future implementation
evidence.
