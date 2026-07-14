# Voice and restricted inference production runbook

This runbook operates the additive local Voice, deterministic fusion and
restricted non-generative inference stack. The Hub remains the only control
plane: clients call Hub APIs, the Hub owns policy, tasks, review, consent,
retention and artifacts, and the isolated runtimes execute only the bounded
operation delegated to them. Voice and restricted-inference runtimes never
delegate to or address each other.

Passing repository tests is evidence for contracts and deterministic fixtures,
not evidence for a particular CPU, GPU or model snapshot. Hardware evidence is
valid only after the explicit hardware gate has run on the promoted manifests.

## Release gates

Run the mandatory, hardware-independent gate from the repository root:

```bash
python scripts/run_voice_restricted_release_gates.py \
  --group core \
  --evidence artifacts/test-gates/voice-restricted-core.json
```

The runner executes the versioned definition in
`config/release-gates/voice-restricted-core.v1.json`. It fails if pytest fails,
collects zero tests, or skips even one selected test. The core covers:

- Hub/worker, Angular/Hub and runtime/runtime architecture boundaries;
- audio and filename attacks, supply-chain validation, SSRF/DNS rebinding,
  remote code, Pickle, envelope tampering, `no_generation`, tenant isolation,
  consent and deletion;
- old/new wire and configuration contracts;
- deterministic German, English and mixed-language fusion goldens;
- versioned benchmark reports and regression thresholds;
- Compose internal-network, credential, mount and image boundaries.

Do not commit generated evidence until the final clean-tree run. A result with
`worktree_dirty=true` is diagnostic, not promotion evidence. Release promotion
fails on any Security, Privacy, offline, `no_generation`, provenance, API,
architecture or no-network regression.

The hardware gate is separate and never part of the P0 Core decision:

Contract gates for optional Judge, diarization/enhancement, streaming and
fine-tuning export are likewise explicit P2 evidence and do not block Core:

```bash
python scripts/run_voice_restricted_release_gates.py --group optional-capabilities
```

Failures still block promotion of that optional capability; its disabled or
unavailable state must preserve the deterministic Core result.

```bash
export ANANTA_RUN_VOICE_RESTRICTED_HARDWARE=1
export ANANTA_VOICE_RESTRICTED_PROFILE=cpu # cpu | rtx-3080 | high-end-gpu
export HUB_PORT=55001 # dedicated, currently unused loopback port for this gate
export ANANTA_VOICE_E2E_AUDIO=/absolute/path/to/licensed-evaluation.wav
export ANANTA_VOICE_E2E_AUDIO_SHA256='lowercase-sha256-of-the-audio-fixture'
export ANANTA_VOICE_E2E_PRIMARY_BACKEND=vosk       # real backend present in the Voice catalog
export ANANTA_VOICE_E2E_SECONDARY_BACKEND=whisper_cpp # distinct real backend, also present locally
export VOICE_WHISPER_CPP_PROMPT_MAX_CHARS=512 # nonzero: forwards bounded classic context
export VOICE_MODEL_DIR=/absolute/path/to/promoted/voice
export RESTRICTED_INFERENCE_MODEL_DIR=/absolute/path/to/promoted/restricted-inference
export ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES='promoted-choice-manifest-id'
# A separate, fully pinned snapshot whose declared RAM requirement exceeds the
# worker budget below. The worker rejects it before constructing an adapter.
export ANANTA_RESTRICTED_INFERENCE_ADMISSION_DENIED_MANIFEST_ID='promoted-over-budget-manifest-id'
export ANANTA_RESTRICTED_INFERENCE_ADMISSION_REASON='ram_budget_exhausted'
export ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES=8589934592
export ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES=0
export INITIAL_ADMIN_PASSWORD='...'
export VOICE_INTERNAL_SERVICE_TOKEN='distinct-random-secret...'
export RESTRICTED_INFERENCE_INTERNAL_TOKEN='another-distinct-random-secret...'
export VOICE_PERSONALIZATION_ENCRYPTION_KEY="$(${PYTHON:-python3} -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

python scripts/run_voice_restricted_release_gates.py \
  --group hardware-compose \
  --evidence artifacts/test-gates/voice-restricted-hardware.json
```

By default this uses preloaded images (`--no-build`) so the deployment can be
tested without package downloads. Set `ANANTA_VOICE_RESTRICTED_BUILD=1` only in
the controlled image-build environment. The gate starts a uniquely named
Compose project and uses the licensed audio through two Hub configuration
profiles. `parallel_compare` is the canonical Hub configuration name for the
parallel-fusion execution path; the gate proves parallel execution, then proves
`classic_then_correct` sequential lineage. It also verifies immutable candidate
provenance, a real Hub-owned `score_choices` child task with
`no_generation=true`, controlled RAM/VRAM admission denial, fail-closed
restricted-worker kill/restart recovery, and failed public egress from both
runtime containers. It tears the stack down unless
`ANANTA_VOICE_RESTRICTED_KEEP_STACK=1` is set for incident analysis.
During the worker outage the Hub still obtains the deterministic two-backend
Voice baseline, records the correlated Restricted child task as failed, and
must not claim that Restricted Choice was applied. After restart the same Hub
profile must again complete a real `score_choices` child task.
The default project name has a random suffix, the gate refuses to adopt an
existing Compose project, and `HUB_PORT` must be an unused unprivileged port.
If setup fails before the tests begin, the partial project is always removed;
containers, networks and project-owned volumes are included. The keep-stack
switch applies only after a ready stack has been yielded.

All manifest IDs above name immutable files in
`$RESTRICTED_INFERENCE_MODEL_DIR/manifests` with matching digest-addressed
snapshots in `artifacts/`. The choice manifest must declare `score_choices` and
be executable by the selected worker image. Select distinct real Voice
backends from the selected Compose profile and include at least one backend
that emits word timestamps (`vosk` or `faster_whisper`), because the gate binds
every emitted word back to its Candidate. The secondary backend is
`whisper_cpp`, whose bounded local prompt consumes the classic transcript and
therefore proves real sequential context in `classic_then_correct`. The
admission-denied manifest must
be distinct, structurally valid and pinned, but its declared `ram_bytes` or
`vram_bytes` must be greater than the matching explicit worker budget. Select
only `ram_budget_exhausted` or `vram_budget_exhausted`. The harness validates
that relationship before starting Compose and refuses an allocation-based OOM
test. On GPU profiles, export the canonical budgets from
`voice-restricted-hardware-profiles.v1.json`; for a VRAM denial, set the reason
to `vram_budget_exhausted` and make the promoted evidence manifest declare more
VRAM than `ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES`.

## Hardware profiles

Canonical budgets are in
`config/release-gates/voice-restricted-hardware-profiles.v1.json`.

| Profile | Compose profile | Minimum evidence host | Intended use |
| --- | --- | --- | --- |
| `cpu` | `voice-production-cpu` | 8 threads, 16 GiB RAM | Vosk/whisper.cpp and small sentence-transformer/ONNX snapshots |
| `rtx-3080` | `voice-production-nvidia` | 8 threads, 32 GiB RAM, 10 GiB VRAM | conservative Faster-Whisper plus one loaded restricted model |
| `high-end-gpu` | `voice-production-nvidia` | 16 threads, 64 GiB RAM, 24 GiB VRAM | optional larger local adapters and bounded parallelism |

These are admission profiles, not performance promises. Record the actual CPU,
RAM, GPU, driver, container runtime, engine versions, quantization, effective
configuration and peak resources in the benchmark report. On a shared RTX 3080,
start with one restricted request and one loaded restricted model; Compose
device reservations do not enforce a VRAM partition between containers.

## Model promotion

1. Acquire models only in a controlled staging environment. Record source,
   license/SPDX identifier, immutable upstream revision and every file digest.
2. Reject mutable revisions such as `latest`, executable model repositories,
   `trust_remote_code`, Pickle/PyTorch checkpoint deserialization, symlinks,
   hardlinks, traversal and files outside the staging root.
3. Build a Voice catalog or restricted-inference manifest with exact sizes and
   SHA-256 values. Use SafeTensors or the explicitly supported local format.
4. Import restricted snapshots through `SecureSnapshotStore`. Promotion creates
   the digest-addressed snapshot only after every file passes validation; a
   failed import must leave no partial/promotable directory.
5. Calibrate against a versioned calibration split. Never tune against holdout.
6. Run core gates, then the applicable hardware gate and benchmark report. For
   fusion/enhancement, promotion requires a holdout report with no metric beyond
   its allowed regression and provenance coverage exactly `1.0`.
7. Make the promoted directories immutable/read-only, update the pinned manifest
   reference and image tag, then deploy. Keep the previous manifest and image
   available for rollback.

## Offline installation

Prepare and sign images plus model bundles before entering the offline zone.
Transfer their digests and SBOM/license records separately. In the target zone:

1. verify image digests and all model-manifest digests;
2. load images into the local Docker daemon/registry;
3. mount Voice and restricted models from distinct read-only roots;
4. set two different internal service tokens and an independent Fernet
   `VOICE_PERSONALIZATION_ENCRYPTION_KEY` through the secret mechanism; expose
   the Fernet key only to the Hub;
5. render every selected profile with `docker compose ... config --quiet`;
6. confirm `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, model download flags
   false, no host runtime ports, and both capability networks `internal: true`;
7. start with preloaded images and no build, then run the hardware gate.

Neither a runtime nor the Hub may repair missing weights by downloading or
substituting a mock transcript. Missing or invalid snapshots stay unavailable.

Generate a local SPDX dependency report for every promoted image and retain it
next to the release evidence:

```bash
python scripts/generate_voice_restricted_sbom.py \
  --image hub=ananta-quickstart-no-ollama:local \
  --image voice=ananta-voice-runtime:local-cpu \
  --image restricted=ananta-restricted-inference:local-cpu \
  --output artifacts/test-gates/voice-restricted-sbom.json
```

The command fails closed on missing/empty SPDX data and additionally rejects
restricted ML engines in the Hub base image. Image IDs and the canonical SPDX
digest bind the dependency report to the exact promoted images.
It uses the `docker sbom` plugin when installed. If the plugin is absent, it
accepts only an SPDX 2.x in-toto predicate already attached by BuildKit
`--sbom=true`; the predicate subject must match the promoted image manifest.
An unattested image therefore still fails closed instead of being reported as
scanned.

## Runtime resource admission

Keep the pre-execution Voice envelope aligned with the selected container limit:
`VOICE_RESOURCE_MAX_RAM_MB`, `VOICE_RESOURCE_MAX_VRAM_MB`,
`VOICE_RESOURCE_MAX_CONCURRENT_BACKENDS`, `VOICE_RESOURCE_MAX_AUDIO_SECONDS` and
`VOICE_RESOURCE_MAX_QUEUE_DEPTH`. CPU/minimal profiles must keep the VRAM budget
at zero. Every promoted Voice manifest must declare conservative RAM/VRAM and
slot requirements; an underestimated manifest is a promotion failure. A
`resource_exhausted` result means the backend did not start and may be retried
only after capacity or policy changes.

The restricted worker receives the explicit
`ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES`,
`ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES`, loaded-model, in-flight and queue
limits through Compose. Hardware-gate exhaustion evidence is an admission
failure from a manifest whose declared requirement exceeds one of those limits;
it is not a request to allocate until the kernel or GPU driver raises OOM.

## Optional isolated generative-judge worker

This capability is off unless the Hub configuration selects `generative_local`
and enables `feature_flags.generative_judge`. It never executes in the Hub.

The standard deployment uses the embedded offline Transformers engine:

1. Place a complete local `AutoModelForCausalLM`/tokenizer snapshot beneath
   `${GENERATIVE_JUDGE_MODEL_DIR}/model`. Do not use remote model IDs, symlinks or
   `trust_remote_code` snapshots. Weights must be Safetensors; pickle-style
   `.bin`/`.pt`/`.pth` and executable model files are rejected. The directory is
   mounted read-only only into the judge worker.
2. Provide `VOICE_GENERATIVE_JUDGE_WORKER_TOKEN` from the deployment secret
   store. It must match the worker token; never put it in a URL or commit it.
3. Keep `VOICE_GENERATIVE_JUDGE_WORKER_URL` and
   `VOICE_GENERATIVE_JUDGE_WORKER_ALLOWED_ENDPOINTS` exactly equal to
   `http://generative-judge-worker:8092/internal/v1/generative-judge`, and keep
   `VOICE_GENERATIVE_JUDGE_HUB_ORIGIN=http://ai-agent-hub:5000`.
4. Start the explicit optional profile with the existing Voice profile, for
   example `docker compose -f compose.voice-restricted.yml --profile
   voice-production-cpu --profile voice-generative-judge up -d --build`.
5. Require `GET /health` inside the judge container to report `ready`. `degraded`
   means auth, origin allowlist or the local engine is not configured; do not
   enable the Hub feature flag.

Legacy `VOICE_GENERATIVE_JUDGE_ENDPOINT` and
`VOICE_GENERATIVE_JUDGE_ALLOWED_ENDPOINTS` values are retained only for config
compatibility and are not execution inputs. They cannot re-enable Hub loopback
judging.

The worker image installs the CPU Transformers runtime separately from the Hub,
uses offline flags, has no peer-worker network and lazily loads the mounted model
inside the worker process. `GENERATIVE_JUDGE_DEVICE=cpu`, bounded input/output
tokens, one in-flight request and the container memory/CPU limits are the default.
For a custom colocated model-server image only, set
`GENERATIVE_JUDGE_ENGINE=loopback` and start that server in the *same container*
before the Flask worker; `GENERATIVE_JUDGE_LOOPBACK_ENDPOINT` accepts only a
literal `127.0.0.1`/`::1` URL. The stock profile uses the embedded adapter and
requires no implicit host service.

The Hub resolves the worker name on every dispatch, rejects any non-private,
loopback or link-local result, pins the accepted address for the HTTP request,
refuses redirects, authenticates with bearer credentials and applies the response
byte limit. The worker exact-matches the Hub `Origin`. Every call has a redacted
Hub-owned child task and correlation echoed by the worker. The response contains
only a Candidate ID—never generated text—so timeout, invalid schema, oversized
response or an invented selection preserves the exact consensus baseline and
fails only the optional child capability.

The embedded adapter checks the absolute Hub deadline before and after model
loading, tokenization and generation, rejects prompts above
`GENERATIVE_JUDGE_MAX_INPUT_CHARS`/`GENERATIVE_JUDGE_MAX_INPUT_TOKENS`, and passes
the remaining deadline to Transformers `max_time` together with
`GENERATIVE_JUDGE_MAX_NEW_TOKENS`. Transformers can stop only between generation
steps; one already-running native forward pass cannot be safely interrupted in
the same process. Therefore CPU/memory limits and `GENERATIVE_JUDGE_MAX_IN_FLIGHT=1`
are part of the safety boundary. Deployments requiring hard per-kernel termination
must use a custom judge-worker image that runs the same `GenerativeJudgeEngine`
contract in a supervised child process and kills that child at the absolute
deadline; they must not move execution into the Hub or another worker.

## Optional isolated generative transcript-corrector worker

This capability is additive and remains inactive unless a resolved Hub Voice
configuration selects `correction_policy=generative_rewrite`, enables
`feature_flags.generative_corrector=true` and names an allowlisted
`generative_corrector_model`. It is a genuine text-to-text rewrite after ASR;
it is not the deterministic postprocessor and it does not replace the
restricted generative Judge.

The Hub remains the control plane. It selects the model, creates and owns a
correlated child task, dispatches one bounded request and persists the result.
The corrector worker executes that single request and never creates tasks,
addresses another worker or receives audio. For a live stream, Vosk partials
remain uncorrected; dispatch happens only when the Hub finalizes the stream. A
batch transcription is corrected after its ASR result is complete.

### Local model catalog

The stock worker loads a local `AutoModelForCausalLM` and tokenizer through the
pinned, offline Transformers engine. Its host model root defaults to
`${GENERATIVE_CORRECTOR_MODEL_DIR:-../../models/generative-corrector}` and is
mounted read-only as `/models/generative-corrector`. The required catalog is
`manifests/model-catalog.json` beneath that root. Its exact envelope and an
example containing Gemma, Phi and another local model are documented in the
[Voice Quickstart](../voice-quickstart.md#1-lokale-modelle-bereitstellen).

Each entry contains exactly `id`, relative `path`, immutable `revision` metadata
and `family`. Every ID exposed by the Hub in
`VOICE_GENERATIVE_CORRECTOR_MODELS` must have an exact catalog match. Additional
catalog IDs remain hidden from clients; keep the two sets intentionally equal
in production for least privilege. Paths may not escape the model root;
symlinks, Python files and Pickle/PyTorch checkpoint formats are rejected, and
every model directory must contain Safetensors weights. Tokenizer/config files
still have to be complete and locally compatible with the pinned engine.

The catalog's `revision` field records promotion provenance but does not by
itself hash every model file. Operators must verify the transferred bundle
against separately retained digests and make the promoted host directory
immutable before deployment. Do not use a mutable upstream branch name as a
revision and do not place weights in Git.

### Compose profile and environment

Start the optional worker together with exactly one selected Voice production
profile. For the stock CPU worker:

```bash
export VOICE_GENERATIVE_CORRECTOR_WORKER_TOKEN='distinct-random-secret-at-least-24-characters'
export VOICE_GENERATIVE_CORRECTOR_MODELS='gemma-2b-it,phi-3-mini-instruct'
export GENERATIVE_CORRECTOR_MODEL_DIR=/absolute/path/to/promoted/generative-corrector

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  --profile voice-production-cpu \
  --profile voice-generative-corrector \
  up -d --build
```

The optional profile name is exactly `voice-generative-corrector`. The stock
image target is CPU-only even when another Voice Runtime profile uses a GPU;
do not set `GENERATIVE_CORRECTOR_DEVICE=cuda` on this image.

Operator-provided or tunable values and their Compose defaults are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `VOICE_GENERATIVE_CORRECTOR_WORKER_TOKEN` | empty/unavailable | independent Hub-to-corrector bearer secret; minimum 24 characters |
| `VOICE_GENERATIVE_CORRECTOR_MODELS` | `gemma-2b-it,phi-3-mini-instruct` | Hub-side model-ID allowlist exposed to clients |
| `GENERATIVE_CORRECTOR_MODEL_DIR` | `../../models/generative-corrector` | host source for the read-only worker mount |
| `VOICE_GENERATIVE_CORRECTOR_TIMEOUT_MS` | `30000` | Hub dispatch timeout, bounded to 1–120000 ms |
| `VOICE_GENERATIVE_CORRECTOR_MAX_RESPONSE_BYTES` | `262144` | Hub response bound, accepted range 1024–2097152 bytes |
| `GENERATIVE_CORRECTOR_DEVICE` | `cpu` | embedded engine device; keep `cpu` for the stock image |
| `GENERATIVE_CORRECTOR_MAX_IN_FLIGHT` | `1` | worker concurrency, accepted range 1–8 |
| `GENERATIVE_CORRECTOR_MAX_INPUT_CHARS` | `32000` | prompt character bound, accepted range 1024–512000 |
| `GENERATIVE_CORRECTOR_MAX_INPUT_TOKENS` | `4096` | tokenized prompt bound, accepted range 128–32768 |
| `GENERATIVE_CORRECTOR_MAX_NEW_TOKENS` | `1024` | output generation bound, accepted range 16–4096 |
| `GENERATIVE_CORRECTOR_MAX_REQUEST_BYTES` | `1048576` | worker request-body bound, accepted range 1024–4194304 bytes |
| `GENERATIVE_CORRECTOR_CPUS` | `4.0` | container CPU limit |
| `GENERATIVE_CORRECTOR_MEMORY` | `8g` | container memory limit |
| `GENERATIVE_CORRECTOR_IMAGE` / `GENERATIVE_CORRECTOR_IMAGE_TAG` | `ananta-generative-corrector-worker` / `local` | promoted worker image reference |

The overlay deliberately fixes the following boundary values; do not turn
them into client configuration:

| Consumer | Fixed value |
| --- | --- |
| `VOICE_GENERATIVE_CORRECTOR_WORKER_URL` and `VOICE_GENERATIVE_CORRECTOR_WORKER_ALLOWED_ENDPOINTS` | `http://generative-corrector-worker:8093/internal/v1/voice-corrector` |
| `VOICE_GENERATIVE_CORRECTOR_HUB_ORIGIN` | `http://ai-agent-hub:5000` |
| `GENERATIVE_CORRECTOR_ALLOWED_HUB_ORIGINS` | `http://ai-agent-hub:5000` |
| `GENERATIVE_CORRECTOR_ENGINE` | `transformers` |
| `GENERATIVE_CORRECTOR_HOST` / `GENERATIVE_CORRECTOR_PORT` | `0.0.0.0` / `8093` (Compose `expose`, no host `ports`) |
| `GENERATIVE_CORRECTOR_MODEL_ROOT` | `/models/generative-corrector` |
| `GENERATIVE_CORRECTOR_MODEL_CATALOG` | `/models/generative-corrector/manifests/model-catalog.json` |
| `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` | `1` / `1` |

The bearer token is copied into the Hub as
`VOICE_GENERATIVE_CORRECTOR_WORKER_TOKEN` and into the isolated worker as
`GENERATIVE_CORRECTOR_INTERNAL_TOKEN`; it is never sent to Angular or Android.
The worker exact-matches the Hub `Origin`. The Hub exact-allows the endpoint,
resolves only private non-loopback container addresses, pins the accepted
address for the request, disables proxy use and refuses redirects. The worker
has only the internal `generative-corrector-control` network and cannot address
the Voice Runtime or restricted worker.

### Readiness, policy and failure behavior

Render the selected profiles before starting them, then inspect readiness from
inside the non-published worker service:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  --profile voice-production-cpu \
  --profile voice-generative-corrector \
  config --quiet

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  exec -T generative-corrector-worker python -c \
  "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8093/health', timeout=3)))"
```

Require `status=ready`, `auth_configured=true`,
`origin_allowlist_configured=true`, `engine_configured=true` and the intended
`model_ids`. Hub `/v1/voice/capabilities` performs the same bounded, pinned
worker-health validation and marks a configured Corrector model available only
when that ready worker reports the exact model ID. It advertises
`generative_transcript_correction` only when at least one model passes. This
readiness probe is still not a replacement for a real transcription smoke test,
because model/tokenizer loading remains lazy until the first correction.

The Hub configuration fields are:

- `correction_policy=generative_rewrite`;
- `feature_flags.generative_corrector=true`;
- `generative_corrector_model=<one exact allowlisted ID>`;
- optional `generative_corrector_max_edit_ratio`, default `0.35`, accepted range
  `0.01`–`1.0`.

Enabling this policy forces `review_policy=always`. The Hub sends at most 8000
characters of baseline transcript and applies the shorter of the Voice request
deadline and Corrector timeout. The worker rejects non-allowlisted models,
expired or overlong deadlines, protected numeric/URL token changes and edits
above the configured ratio. It returns original text, corrected text, structured
edits, model revision, engine and prompt provenance.

Any unavailable worker, timeout, invalid envelope, model mismatch, unsafe edit,
tracking failure or generation failure preserves the exact ASR baseline and
records a Corrector fallback reason. Never silently promote a fallback as a
corrected transcript. Lazy first-model loading can consume much of the initial
deadline, so benchmark both cold and warm requests on the promoted hardware.

During rollback, first disable `feature_flags.generative_corrector` or change
the profile to deterministic correction, drain active Voice streams, and only
then stop the optional worker. Existing Vosk/batch transcription continues
through the Hub without the generative rewrite. Changing the selected model is
a Hub policy operation; Angular and Android clients must never receive a model
path or worker address.

## Streaming drain and maintenance

Before restart or manifest switch, prevent the Hub from admitting new Voice
streams (maintenance policy/load-balancer drain). Keep the Hub available for
status/finalize/delete calls. Poll Hub-owned stream sessions until active count
is zero or the documented session deadline expires. Finalize complete sessions;
delete expired/abandoned sessions through the Hub. Then stop the Voice runtime
with its Compose grace period. Never copy runtime in-memory buffers into Hub
logs or persistent model directories.

After restart, verify health, capability catalog and manifest digests before
opening admission. A client retries only with its original idempotency key and
chunk sequence. Replayed chunks must be byte-identical; sequence conflicts are
incidents, not automatic overwrite requests.

The Hub stages an encrypted provisional cleanup target before every Runtime
stream create. Startup recovery processes provisional targets because their
in-memory Hub capability mappings cannot survive a process restart. Periodic
housekeeping ignores provisional targets belonging to the current process and
only retries cleanup work that has been activated by finalize, expiry,
revocation or deletion.

## OOM and resource exhaustion

Expected typed failures include queue full, timeout, RAM budget exhausted, VRAM
budget exhausted and out of memory. On one of these:

1. stop new admission in the Hub and preserve request/task IDs only;
2. inspect runtime status/resource counters, container memory events and GPU
   process memory without dumping input text or tensors;
3. wait for in-flight leases to release, then use the authenticated Hub-mediated
   unload/cache-GC operation where available;
4. restart only the affected runtime if its allocator remains unhealthy;
5. reduce concurrency/model count or select a smaller already-promoted snapshot;
6. rerun provenance, no-network and resource benchmark gates before reopening.

Do not silently move model execution into the Hub, enable CPU fallback contrary
to policy, or increase limits until the container can affect neighboring
services.

## Runtime metrics and correlation

The Voice runtime exposes Prometheus data at `GET /metrics`. The endpoint is
available only on the internal Voice control network and requires the same
`X-Ananta-Internal-Token` service identity as execution endpoints. Do not
publish the runtime port or put the token in a scrape URL; configure the
collector's authenticated request header through its secret mechanism.

Runtime metrics cover bounded HTTP outcomes, local backend latency/errors,
candidate-capacity wait, audio duration, real-time factor, deterministic fusion,
fallback/rerun, stream events/chunk sizes, backpressure and circuit-breaker
transitions. Labels use fixed enumerations. Request IDs, tenants, filenames,
model paths/IDs, audio, transcripts, exception text and secrets are never metric
labels.

The Hub forwards `X-Request-ID`; the runtime returns it as a response header and
writes it to its content-free request log. Correlate Hub and runtime incidents
with that ID in logs, not Prometheus labels. Each request log also records the
bounded `store_audio_requested` and `store_audio_effective` state. The current
runtime never persists raw audio, so effective storage remains false even when
a legacy/requested setting is true.

## Rollback

Rollback is additive and reversible:

1. disable the independent fusion/restricted/adaptive/personalization feature
   flag at the Hub; do not rewrite stored sparse settings;
2. drain streams and queued restricted work;
3. restore the previous pinned manifest/catalog and immutable image tag;
4. recreate only the affected runtime containers;
5. verify capability status and digest, then run Core plus the prior profile's
   smoke/benchmark gate;
6. keep new-format artifacts readable through the backward-compatible Hub API.

Never amend already published evidence. Create a new failed/rollback evidence
record that references the affected report ID and manifest digest.

## Consent revocation, reset and deletion

Normal review never enables learning. A user must separately opt in to explicit
categories, purpose, consent version and retention. All calls use the Hub and an
idempotency key.

- Revocation: set the profile consent to `granted=false`. New personalization
  snapshots and feedback are blocked immediately; runtimes never retain a copy.
- Feedback reset: delete the profile's personalization endpoint. This removes
  learned feedback but is not a full privacy deletion.
- Full deletion: invoke the confirmed Voice privacy-delete endpoint. It revokes
  consent/snapshots and removes reviews, encrypted result artifacts, feedback,
  idempotency records and other profile-scoped Voice data according to policy.
- Verification: export after deletion must contain no deleted items, a second
  tenant must never observe the profile, and idempotent replay must not create a
  second artifact.

Voice mutation idempotency is bounded by `VOICE_IDEMPOTENCY_TTL_SECONDS`
(default one day, hard maximum 30 days). Audio conflict detection uses an
operation- and idempotency-key-specific HMAC, never a reusable audio digest;
database keys are HMAC-digested as well. Hub housekeeping physically removes
expired completed and pending rows.

Full deletion first appends a keyed, pseudonymous scope tombstone to
`VOICE_DELETION_LEDGER_PATH`. The ledger contains only HMAC digests, cut-off
timestamps, an integrity chain and HMAC-digested idempotency keys; it contains
no tenant, owner, profile, runtime-session, transcript or audio value. The Hub
database contains only a rebuildable projection of this ledger. At startup the
Hub imports the external ledger, finds restored Voice scopes by keyed digest,
and deletes only records whose `created_at` is not newer than the deletion
cut-off. A later explicit consent and newly created data therefore survive.

The ledger rotates immutable segments after
`VOICE_DELETION_LEDGER_SEGMENT_RECORDS` entries and caches its integrity-checked
key index, so normal claims do not rescan the complete history. The hard
`VOICE_DELETION_LEDGER_MAX_RECORDS` limit bounds retained control data; reaching
it fails new deletion claims closed with `voice_deletion_ledger.capacity_exhausted`.
Reconciliation reads the database projection with a stable `(deleted_at, id)`
cursor until every page has been processed, rather than repeatedly processing
only the oldest fixed-size page.

The ledger is deliberately outside the database restore boundary. A database
snapshot from before deletion cannot preserve a tombstone by itself. Operators
must keep `/app/data/voice-deletion-ledger.v1.jsonl` and every adjacent
`.segment-*` file on the Hub data volume and
back it up separately with the same or stronger durability as database backups.
For a restore:

1. stop the Hub and preserve the current ledger separately;
2. restore the database without replacing or rolling back the ledger;
3. restore the preserved ledger with owner-only permissions if the data volume
   was replaced;
4. start the Hub with the same stable `SECRET_KEY` used to sign the ledger;
5. require successful startup reconciliation and verify that no cleanup outbox
   row reports `failed` before reopening Voice traffic.

Never restore the ledger from the older database snapshot. A missing, malformed
or HMAC-invalid ledger is a fail-closed restore error, not permission to serve
possibly resurrected Voice data.

Do not place spoken text, corrections, exports or audio in tickets or command
history. Record only tenant-safe opaque IDs, counts and reason codes.

## Incident diagnosis

Capture the Git SHA, gate ID, profile ID, image and manifest digests, request/run/
task IDs, reason code, container health, queue/resource counters and redacted
decision trace. Do not capture raw audio, transcript/candidate text, auth headers,
personalization content, model tensors or service tokens.

Check boundaries in this order:

1. Hub authorization, tenant binding, consent and idempotency audit;
2. Hub policy/config precedence and capability reason code;
3. internal service authentication and request correlation;
4. runtime manifest admission and `synthetic=false` provenance;
5. deadline, queue, RAM/VRAM and stream sequence state;
6. internal networks and unexpected egress;
7. deterministic replay against the same fixture/configuration.

Quarantine the affected manifest and disable its capability if provenance,
hashes, tenant isolation, `no_generation` or network boundaries are uncertain.
Treat missing source identifiers as unverified; never invent `SRC_*` or `RUN_*`
identifiers in the incident record.
