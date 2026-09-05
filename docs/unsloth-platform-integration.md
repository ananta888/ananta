# Unsloth platform integration

## Scope

Ananta integrates Unsloth as a set of additive capabilities. The Hub remains
the control plane and owns admission, policy, tasks, approval, and promotion.
Only a delegated worker may load a model, train, download, or export model
weights.

The integration is split into independently gated profiles:

| Profile | Purpose | Default behavior |
| --- | --- | --- |
| Core text | Local QLoRA/LoRA training and immutable artifacts | Fail closed unless a compatible NVIDIA worker reports the backend |
| Modalities | Vision, audio, and embedding training | Each modality has its own capability and reason code |
| Studio | Optional read projection and Hub-mediated commands | Disabled until endpoint, network, TLS, and authentication policy all pass |
| MCP | Allowlisted administrative Studio tools | Disabled, authenticated, role checked, replay protected |
| Release | CPU contract gate and headless GPU acceptance | No GPU claim without a real attested run |

Disabling any optional profile must not make the existing PEFT/TRL or mock
training paths unusable.

## Control flow

```text
Angular / external caller
        |
        v
Hub authentication, tenant policy, idempotency, audit
        |
        v
Hub task queue
        |
        v
Hub-initiated task-scoped HTTP dispatch
        |
        v
Dedicated training, import, recipe, or Studio adapter worker
        |
        v
Immutable artifact publication
        |
        v
Hub evaluation and explicit promotion
```

Workers never submit work to other workers. Studio is not a second control
plane: mutating Studio operations are represented as Hub commands and remain

Model import and data-recipe tasks use the existing central queue and
task-scoped HTTP dispatch (`/tasks/<id>/step/propose` followed by
`/tasks/<id>/step/execute`). Workers do not poll a queue. Their closed
`ananta.unsloth-worker-task-result.v1` response returns synchronously through
the existing Hub `persist_forwarded_execution(...)` completion path.

The Compose deployment has two independent execution profiles:

| Compose profile | Capability | Network and filesystem boundary |
| --- | --- | --- |
| `unsloth-model-import-network` | `unsloth_model_import` | Separate explicit egress network, Hugging Face snapshot allowlist, read-only local artifact mount, writable immutable cache mount |
| `unsloth-data-recipe` | `unsloth_dataset_materialization` | Hub-only internal network, offline library switches, read-only dataset mount, task-bound deterministic output paths |

The recipe worker validates the exact Hub task ID from every dispatched,
hash-bound task context and publishes each immutable recipe under its
deterministic recipe ID. `ANANTA_UNSLOTH_RECIPE_ATTEMPT_OUTPUT_DIR` selects the
writable host boundary for those task-bound outputs; no task ID is injected
through static container configuration. The model-download worker is
unavailable unless the separate `unsloth-model-import-network` profile is
explicitly selected and `ANANTA_UNSLOTH_MODEL_NETWORK_ENABLED=true` is present
in its container composition.
subject to the same tenant, confirmation, and audit rules.

## Configuration contract

The Unsloth security contract lives below
`ml_intern_training.unsloth_security` and accepts only these fields:

| Field | Meaning |
| --- | --- |
| `operating_mode` | Selected, validated Unsloth operating profile |
| `model_downloads_enabled` | Separate opt-in for worker-side network downloads |
| `remote_tunnel_enabled` | Separate opt-in for any remote tunnel |
| `code_execution_enabled` | Separate opt-in for executable Studio tools |
| `mcp_enabled` | Separate opt-in for the MCP control surface |
| `studio_url` | Policy-validated Studio endpoint |
| `auth_secret_ref` | Reference to authentication material, never plaintext |
| `tls_required` | Transport TLS requirement |
| `allowed_hosts` | Exact Studio host allowlist |
| `require_grounded_provenance` | Require trusted source and run references |
| `trusted_source_ids` | Authoritatively supplied `SRC_*` catalog |
| `trusted_run_ids` | Authoritatively supplied `RUN_*` catalog |

`external_network_allowed` remains an independent top-level training flag.
Enabling a feature-specific flag does not implicitly enable network access,
tunnels, code execution, or MCP. Unknown fields and conflicting combinations
are rejected with stable reason codes.

Workers and callers never generate a `SRC_*` or `RUN_*` identifier. The Hub
Evidence Registry may issue and reserve them automatically after admission. A missing,
malformed, or unknown identifier is unverified and blocks any grounded claim.

## Worker image and hardware policy

The optional NVIDIA worker is pinned to CUDA 12.4, PyTorch 2.6, Unsloth
2026.7.5, and Unsloth Zoo 2026.7.6. Its dependency set follows the constraints
published for that Unsloth release. Mock and CPU images are separate sibling
targets and do not inherit the Unsloth/CUDA stack.

The `rtx3080-safe` admission profile is conservative:

| Limit | Value |
| --- | --- |
| Nominal VRAM | 10 GiB |
| Reserved headroom | 1 GiB |
| Maximum sequence length | 2048 |
| Per-device batch | 1 |
| Gradient accumulation | 32 |
| Maximum LoRA rank | 32 |
| Weight loading | 4-bit required |

Admission happens before model loading. Estimated oversubscription, unavailable
CUDA, an unsupported driver, and kernel import errors are stable domain
failures rather than an automatic CPU fallback.

Progress events may include loss, step, throughput, allocated VRAM, peak VRAM,
and GPU utilization. Consumers must tolerate absent optional resource metrics.

## Data and model sources

A Data Recipe manifest binds all normalized training fields to a tenant-owned,
approved dataset snapshot and its lowercase SHA-256 digest. Secret scanning,
PII review, license review, non-empty content, deterministic split seed, and
objective-specific media mapping are checked before a recipe can be used.

Model sources use a two-step plan/confirm contract:

1. The Hub validates an opaque local artifact ID or a Hugging Face model ID
   pinned to an immutable commit revision.
2. The caller confirms the unchanged canonical plan digest.
3. The Hub submits the I/O operation as a task.
4. The worker preflights remote file sizes, materializes into a staging
   directory, verifies the expected tree digest, and publishes atomically.

The training worker itself remains offline. A deployment that enables remote
model downloads must route those tasks to a separately network-authorized
worker and must keep `trust_remote_code` disabled.

## Checkpoints and exports

An Unsloth training request can contain one to eight export entries:

```json
{
  "backend": "unsloth",
  "allow_merge": true,
  "exports": [
    {"format": "adapter"},
    {"format": "merged_16bit"},
    {"format": "gguf", "quantization_method": "q4_k_m"}
  ]
}
```

Supported GGUF methods are `q4_k_m`, `q5_k_m`, and `q8_0`. Merge and GGUF
exports require `allow_merge=true`. Callers cannot provide filesystem
destinations. The worker derives attempt-scoped destinations, writes into a
staging directory, hashes all payload files, writes a provenance manifest, and
publishes with an atomic rename.

Every export is bound to tenant scope, job, attempt, dataset identity, base
model snapshot, format, and artifact digest. Existing artifact admission
validates the resulting tree before the Hub registry can expose it.

## Evaluation, promotion, and runtime handoff

Evaluation and promotion are separate responsibilities. Promotion requires:

| Gate | Required state |
| --- | --- |
| Evaluation | Completed and passed |
| Tenant | Evaluation and artifact tenant match |
| Artifact | ID and SHA-256 match the evaluated artifact |
| Dataset | SHA-256 matches the evaluated dataset |
| Metrics | Every configured minimum is met |
| Evidence | All source and run IDs are in the trusted catalogs |
| Registry | Expected revision matches |
| Confirmation | Canonical promotion plan digest is unchanged |

Runtime handoff is provider neutral. It accepts only a promoted, digest-verified
artifact and an explicit endpoint revision fence. It submits a Hub task and
never starts a provider process itself. There is no implicit runtime fallback.

## Studio transport

The Studio profile is isolated in `docker/compose-next/compose.unsloth.yml`.
It requires an image digest, has no host port, Docker socket, SSH service,
Jupyter surface, public tunnel, or privileged container mode. The upstream
process is started with the documented `--disable-tools` switch. Omitting
`--secure` and `--cloudflare` is the tunnel control; undocumented environment
variables are not treated as security controls.

The Hub transport applies:

| Control | Limit |
| --- | --- |
| Host and resolved IP | Explicit allowlists |
| DNS | Bounded resolver using the request deadline |
| Connect timeout | At most 2 seconds |
| Total timeout | At most 10 seconds |
| Decompressed response | At most 1 MiB |
| Redirects | Rejected |
| Retries | At most one and only for idempotent GET |
| Credentials | Secret reference, redacted from output |

Studio currently authenticates its API with JWT login and refresh. A static
secret is not assumed to be a permanent JWT. The Studio capability must remain
unavailable until the deployment provides a confirmed login/refresh
composition or an externally managed valid bearer reference.

## MCP policy

The MCP adapter is default deny. A tool must have an explicit access class,
allowed roles, and, for administrative tools, an explicit confirmation. Every
mutation requires a tenant, actor, bearer secret, idempotency key, bounded
replay window, and unused replay nonce. Outputs are bounded and redacted.

The upstream `/mcp` endpoint is never exposed directly to Angular or an
external caller. MCP commands enter through the Hub and are rejected when the
MCP capability is unavailable.

## Angular projection

The existing `/model-training` feature projects `unsloth.core`,
`unsloth.studio`, `unsloth.mcp`, `unsloth.release_profile`,
`unsloth.modalities.*`, and `unsloth.operations.*` from the Hub capability
response. Missing facets render as unavailable with a reason code and do not
disable the core training surface.

Export, runtime handoff, and MCP controls use opaque resource IDs and a
dry-run/confirm sequence. The browser constructs URLs only from the existing
Hub facade plus fixed relative routes. It cannot accept a Worker URL, Studio
URL, or filesystem path.

## Release evidence

The release workflow has two independent gates:

| Gate | Trigger | Claim |
| --- | --- | --- |
| CPU contract gate | Pull request and push | Contracts, policy, and dry-run behavior only |
| GPU acceptance | Headless, self-hosted NVIDIA runner | Real local Unsloth smoke result |

The GPU attestation records build digest, runtime image digest, dependency
versions, CUDA/cuDNN data, peak VRAM, and supplied evidence IDs. Missing
`SRC_*` or `RUN_*` values keep verification false. Branch protection and runner
registration are repository-administration tasks and cannot be established by
application code.

See `docs/unsloth-release-gates.md` for workflow inputs and attestation output.

## Licensing boundary

Unsloth documents a dual-license boundary: the core package is Apache-2.0,
while optional components including the Studio UI are AGPL-3.0. Unsloth Zoo
publishes an LGPL-3.0-or-later package license. The Studio compose profile is
therefore separate and opt-in; deploying or modifying it requires the operator
to satisfy the applicable AGPL obligations. Model and dataset licenses remain
independent inputs and must pass Ananta's license review.

Upstream references:

- [Unsloth README and license](https://github.com/unslothai/unsloth/blob/main/README.md?plain=1)
- [Unsloth 2026.7.5 package metadata](https://pypi.org/project/unsloth/2026.7.5/)
- [Unsloth Zoo 2026.7.6 package metadata](https://pypi.org/project/unsloth-zoo/2026.7.6/)
- [MCP endpoint merge](https://github.com/unslothai/unsloth/pull/7191)
- [FastSentenceTransformer integration](https://github.com/unslothai/unsloth/pull/3719)

This documentation is an engineering boundary description, not legal advice.
