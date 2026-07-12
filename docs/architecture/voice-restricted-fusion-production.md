# Voice, Restricted Inference and Fusion Production Architecture

Status: normative for the `voice-production-restricted-inference-and-fusion-2026-07-12`
track. The existing voice and restricted-inference documents remain useful migration
references; this document wins where they differ.

## Baseline and delta matrix

`State` is normative: `implemented` means code and non-hardware contract evidence
exist, `partial` means the named production evidence is still outstanding, and
`missing` means no implementation boundary exists. Every open gap appears once
and has exactly one follow-up task; completed implementation may cite several
owning tasks.

| Area | Baseline on 2026-07-12 | Production delta | State | Implemented evidence | Open gap / unique follow-up | Owner |
|---|---|---|---|---|---|---|
| Hub voice API | Authenticated batch endpoints and a runtime HTTP adapter existed | Bounded upload, tenant idempotency, deadlines, internal authentication and stable envelopes | implemented | VPRF-009, VPRF-024–VPRF-027 | — | Hub |
| Audio lifecycle | Uploads were read completely and passed as opaque bytes | Signature validation, bounded decode, absolute PCM timeline, ephemeral workspaces and consent-bound retention | implemented | VPRF-010–VPRF-012, VPRF-068, VPRF-072 | — | Voice runtime + Hub governance |
| Vosk batch | Optional adapter boundary existed | Real batch recognizer, word timing, bounded resources and typed readiness | partial | Adapter and conformance boundary exist | Real pinned-model offline evidence: VPRF-014 | Voice runtime |
| whisper.cpp | Optional subprocess adapter existed | Immutable executable/model validation, safe argv, bounded subprocess, JSON parsing and provenance | implemented | VPRF-015 | — | Voice runtime |
| Faster Whisper | No adapter existed | Optional lazy local adapter; never part of the minimal installation | partial | Lazy adapter and unavailable contracts exist | Real CPU/CUDA evidence: VPRF-016 | Voice runtime |
| Voxtral | Deterministic simulation could look like a result | Simulation is test-only; production requires a real pinned local adapter and successful runtime probe | implemented | VPRF-017 | — | Voice runtime/native client |
| Hub-mediated streaming | A capability flag and batch alias existed | Versioned state machine, ordered chunks, backpressure, partial/final events and cleanup | implemented | VPRF-022 | — | Hub + voice runtime |
| Incremental Vosk streaming | No real incremental recognizer evidence existed | Native chunks, partial-before-final, cancellation and cleanup | partial | Protocol and recognizer boundary exist | Real incremental offline evidence: VPRF-023 | Voice runtime |
| Confidence rerun | Full audio was rerun and the first result replaced | Only low-confidence PCM regions are rerun and merged on the absolute timeline | implemented | VPRF-021 | — | Voice runtime |
| Optional diarization | Only placeholder segmentation existed | Manifest-bound local adapter with stable overlap assignment and ASR-safe failure | partial | Adapter, manifest and failure contracts exist | Real licensed-model evidence: VPRF-031 | Voice runtime |
| Restricted inference | Adapters and registry ran in the Hub namespace | Immutable Hub port and isolated worker own heavyweight inference | implemented | VPRF-033, VPRF-037–VPRF-048 | — | Hub + restricted runtime |
| Model supply chain | Model strings and local paths were accepted | Manifest, allowlisted roots, immutable digest, pinned revision, safe format, license and SBOM metadata | implemented | VPRF-034–VPRF-036 | — | Hub policy + restricted runtime |
| Restricted security/recovery E2E | No isolated real-model gate existed | Hub queue, worker recovery, OOM/cancellation and offline security proof | partial | Non-hardware security contracts exist | Real isolated-model recovery evidence: VPRF-049 | Test/benchmark tooling |
| Fusion | No shared fusion contract existed | Bounded candidates, lineage, alignment, calibration, deterministic consensus and per-word provenance | implemented | VPRF-050–VPRF-065 | — | Voice runtime |
| Review, consent and learning | No shared governance lifecycle existed | Tenant-bound immutable review, consent, encrypted feedback, personalization and deletion | implemented | VPRF-066–VPRF-073 | — | Hub |
| Angular | Hub voice client and separate native Voxtral page existed | Canonical Hub-only settings, comparison, review, consent, personalization and diagnostics | implemented | VPRF-074–VPRF-078, VPRF-085 | — | Angular client |
| Voice quality benchmarks | No corpus contract existed | Licensed split manifests and WER/CER/RTF/resource reports | partial | Dataset, metric and report contracts exist | Real Voice benchmark evidence: VPRF-030 | Test/benchmark tooling |
| Cross-system no-network Compose | No Voice/Restricted/Fusion deployment proof existed | Hub queue plus internal runtimes with blocked external egress and complete provenance | partial | Compose and gate harness exist | Real Compose evidence: VPRF-083 | Hub + runtimes + test tooling |
| Release promotion | No combined release decision existed | Versioned core/hardware gates, runbooks, thresholds and immutable evidence | partial | Gate runner, profiles and runbooks exist | Clean CPU/NVIDIA promotion evidence: VPRF-087 | Release tooling |

No production delta introduces a second registry, runtime, task queue or
persistence owner. The table contains no currently `missing` boundary; all
remaining gaps are explicitly `partial` and assigned to one follow-up task.

## Ownership and trust boundaries

| Responsibility | Hub | Voice runtime | Restricted runtime | Angular |
|---|:---:|:---:|:---:|:---:|
| Authentication, RBAC and exposure policy | owns | verifies internal token | verifies internal token | presents identity |
| Tenant, profile and session scope | owns | receives minimized projection | receives immutable request scope | requests through Hub |
| Task queue, routing permission and deadlines | owns | obeys envelope | obeys envelope | no ownership |
| Audio decode, VAD and ASR | delegates | owns execution | none | capture only |
| Restricted model execution | delegates | never calls worker | owns execution | never calls runtime |
| Candidate fusion | sets policy bounds | deterministic execution | optional typed scores only | renders result |
| Consent, review, feedback and retention | owns | stateless | stateless | manages through Hub API |
| Durable persistence and audit | owns | metrics/redacted trace only | metrics/redacted trace only | none |

Workers and runtimes do not delegate work to one another. In particular, voice
fusion cannot call the restricted worker directly: an optional judge request is a
new Hub-owned task and its fixed-choice response can only be applied through the
Hub policy envelope.

## Versioned capability catalog

The wire object is `ananta.model-capability.v1` and uses these required fields:

```json
{
  "schema_version": "ananta.model-capability.v1",
  "id": "whisper-base-de",
  "engine": "whisper_cpp",
  "revision": "sha256:...",
  "tasks": ["transcription"],
  "languages": ["de", "en"],
  "device": "cpu",
  "quantization": "q5_1",
  "license": "MIT",
  "status": "ready",
  "manifest_digest": "sha256:...",
  "extensions": {
    "voice": {"batch": true, "streaming": false, "word_timestamps": true}
  }
}
```

`status` is one of `ready`, `degraded`, or `unavailable`. Voice-specific and
restricted-inference-specific features live below separate extension keys. A
runtime publishes a snapshot; consumers cannot mutate its registry.

## Voice result and lineage contract

`TranscriptionResult` remains backward compatible (`provider`, `model`, `text`,
`language`, `duration_ms`, `warnings`) and adds candidates, selected candidate,
strategy, disagreement regions, trace and word provenance. Every candidate has a
stable request-scoped opaque ID, backend/model revision, execution location, audio
variant, parent/source candidate IDs, words, segments, confidence, latency and a
typed error. Candidate IDs deliberately exclude latency and scheduling order,
but include a random per-request lineage token so equal audio cannot be linked
across requests.

An audio variant is identified by a request-local nonce plus the deterministic
enhancement recipe; neither the variant ID nor its persisted metadata contains a
raw PCM digest. Candidates sharing the same request-local audio lineage are
correlated and must not be counted as independent votes. A final word is legal
only if it points to a source word/candidate or to an explicit deterministic edit
operation. The fusion hash excludes volatile timing values and is reproducible
only inside the same declared lineage, not as a cross-request audio fingerprint.

## Orthogonal strategy configuration

The canonical axes are:

- `transport_mode`: `batch` or `streaming`
- `recognition_strategy`: `single`, `parallel_compare`, `classic_then_correct`
- `routing_strategy`: `fixed`, `fallback`, `adaptive`
- `correction_policy`: `none`, `deterministic`, `restricted_choice`, `generative_local`
- `review_policy`: `automatic`, `on_disagreement`, `always`

Precedence is session delta, profile delta, global configuration, then safe
runtime defaults. Deltas are sparse and rollback never deletes them. Legacy
`VOICE_TRANSCRIPTION_PIPELINE` values are mapped at the boundary. `manual_review`
is a Hub workflow state and never an ASR mode.

Legacy migration is additive and deterministic:

| Legacy input | Canonical projection | Mixed-config rule |
|---|---|---|
| `transcription_pipeline` / `VOICE_TRANSCRIPTION_PIPELINE` | `transport_mode` plus the compatible `recognition_strategy`; the Runtime retains the legacy stage selector | Explicit canonical transport or recognition fields win |
| `backend_fallback_order` / `VOICE_BACKEND_FALLBACK_ORDER` | First supported backend becomes `primary_backend`, remaining supported entries become ordered `secondary_backends` | Explicit canonical primary or secondary fields win independently; development-only `mock` is retained only inside the Runtime |
| `*_enabled` Voice booleans | Corresponding member of `feature_flags` | Explicit members of `feature_flags` win individually |
| Top-level `embedding_*` fields | Fill missing sentence-transformers model/language options | They never implicitly enable Restricted Inference; explicit nested `restricted_inference` values win |

Disabling a feature flag applies only an effective compatibility fallback. It
does not rewrite sparse global, profile or session deltas, so re-enabling the
flag restores the previously requested configuration without data migration.

Independent flags default to false for fusion, restricted-worker execution,
CodeCompass reranking, personalization, generative judging and optional model
downloads. Disabling a flag selects the compatible single-backend or deterministic
ranking path without rewriting stored settings.

### Scope identity and composition

Profile and session deltas are sibling, sparse overlays in the version-1 contract.
Their durable identity is the authenticated tenant and subject plus `scope` and
`scope_id`; a session delta does not own or persist a `profile_id`. The Hub combines
an independently selected profile overlay and session overlay only while resolving
an effective preview or recognition context. Accordingly, the profile field shown
in Angular's session view selects the inherited preview context and is not part of
the session-delta persistence key.

Reusing one session ID for the same principal deliberately selects the same session
overlay, regardless of the profile combined with it. Clients therefore must create
a distinct session ID for each logical voice session and must not reuse a session ID
across profiles. This also keeps profile privacy deletion unambiguous: the Hub removes
session overlays related to the deleted profile through its reviews, streams and
tasks. A future explicit profile binding would require an additive versioned contract
and migration; it must not silently reinterpret existing version-1 session keys.

### Runtime execution-policy projection

The Hub sends the resolved profile/session configuration only inside
`recognition_context.configuration`. The runtime accepts exactly the canonical
strategy, backend, budget and feature-flag fields. Unknown fields—including model
paths, service tokens, device allocation and download policy—reject the request.
Runtime maxima are an upper envelope: Hub deadlines, parallelism and candidate
counts are clamped downward and Hub-selected backends must belong to
`VOICE_POLICY_ALLOWED_BACKENDS`. A disabled `voice_fusion` flag changes a requested
parallel strategy to `single` and records the reason without mutating Hub state.

The same projection is accepted when a Hub stream is created. Policy-bearing
streams use the normal transcription pipeline at finalization, so backend choice,
enhancement, deterministic fusion and regional routing are identical to batch.
Incremental single-backend partials remain available for legacy streams without a
policy context. Partial multi-model consensus during an active stream is a separate
optional capability and is not implied by finalization-time fusion.

### Enhancement, diarization and adaptive routing

`VOICE_ENHANCEMENT_VARIANTS` is an explicit ordered allowlist of `original`,
`bypass`, `normalized`, `high_pass` and `speech_safe`. It is active only with
`VOICE_AUDIO_ENHANCEMENT_ENABLED=true` and Hub-enabled fusion. PCM-identical
variants are not executed twice. Derived candidates point to their backend's
original candidate and share its lineage, so correlated variants contribute at
most one vote per lineage. Before fusion, the lineage validator rejects duplicate
IDs, missing parents, cycles, unreachable roots, source-audio changes and
inconsistent audio-variant/model provenance.

Pyannote is selected only by `VOICE_DIARIZATION_BACKEND=pyannote` together with a
local `ananta.voice-diarization-manifest.v1`, an allowlisted model root, a pinned
revision, declared license, bundle digest and offline runtime flags. Any dependency,
manifest or inference failure preserves the ASR segments and their word timings.

`adaptive_local` consumes only calibrated confidence and Hub-allowed local
backends/devices. Runtime latency and regional-duration limits cap the Hub envelope.
Only bounded low-confidence PCM regions are rerun; missing or failed replacements
leave baseline segments unchanged.

### Correction boundary and typed backend tuning

Voice runtime never calls the restricted worker or a generative endpoint. Both
`restricted_choice` and `generative_local` are reported as
`hub_postprocessing_required`; the Hub owns delegation, validation and application.
The generative path uses the transport-neutral `GenerativeJudgeWorkerPort`. The
Hub creates a redacted child task before every call, copies only verified opaque
privacy scope from the Voice parent, and delegates to a dedicated worker on the
internal `generative-judge-control` network. The wire response contains a known
Candidate ID and no generated text. The Hub pins the worker DNS result to a
private container address, sends bearer authentication and an exact allowlisted
Hub `Origin`, refuses redirects and bounds response bytes. Invalid correlation,
public/loopback/link-local resolution, oversized output or an unknown Candidate
ID preserves the byte-identical consensus result. Trace metadata always records
`execution_owner=worker`.

The default optional worker image contains an embedded offline Transformers
adapter and loads a read-only local causal-LM snapshot lazily inside its own
process. It has no task queue, Hub modules, peer-worker network or remote download
path. A loopback model-server adapter also exists exclusively in that worker
image for custom colocated images; the standard Compose profile does not depend
on it or on a host service. Without an injected/configured engine and service
token the worker reports `degraded` and execution remains unavailable.

Backend execution is admitted before model invocation against immutable RAM,
VRAM, concurrency, audio-duration and queue budgets. Model manifests publish
their conservative requirements; a Hub request may narrow, but never expand,
the runtime envelope. The Compose profiles set explicit RAM/VRAM/concurrency
limits below their container limits.

whisper.cpp tuning is expressed through typed bounded fields for threads, GPU
layers, beam size, temperature and prompt characters. Faster-Whisper exposes typed
compute type, beam size and VAD settings. Arbitrary whisper arguments remain
forbidden in production.

## Production readiness and provenance

Production activation is fail-closed:

- mock or synthetic backends are rejected;
- model revisions and manifests must be pinned by digest;
- remote code, pickle-style unsafe deserialization and implicit downloads are denied;
- executable and model paths must resolve beneath configured roots;
- results identify engine, revision, manifest digest, device, execution location
  and `synthetic=false`;
- missing models report `unavailable`; no plausible replacement transcript is
  manufactured.

The minimal installation imports no optional VAD, diarization, GPU, Transformers,
Vosk or Whisper package during Hub startup.

## Evaluation contract

Dataset manifests use `ananta-evaluation-dataset.v1`. Each sample or corpus records
source, license/SPDX expression, SHA-256, language, data classification, consent
basis and one of the immutable `ci`, `hardware`, or `holdout` splits. Holdout data
must never tune calibration.

Voice metrics: WER, CER, named-entity accuracy, number accuracy, timestamp error,
ECE, Brier score, real-time factor, latency, peak RAM and peak VRAM. Restricted
metrics: accuracy, macro/micro F1, ranking MRR/nDCG, calibration, latency and memory.
Calibration artifacts are addressed by backend, model revision and dataset
version and are rejected on any key mismatch.

## SOLID review

- SRP: Hub policy/persistence, audio execution, restricted execution, fusion and UI
  are separate components.
- OCP: backends and judges implement small ports and registries; public envelopes
  gain additive fields.
- LSP: unavailable optional adapters return typed readiness/errors and never
  silently change semantics.
- ISP: voice capabilities, restricted capabilities, review and consent are focused
  contracts instead of a shared god interface.
- DIP: Hub services depend on immutable transport ports and repositories, not ML
  frameworks or runtime implementations.

Known legacy coupling is retained only at compatibility adapters. Moving production
model loading out of the Hub removes the most important existing SRP/DIP violation.
