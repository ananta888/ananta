# Adaptive growth, Jeff, Jev and Vision implementation

The requested scope is four tracks. Adaptive growth is a research-to-MVP track,
not a promise to train a 3B–30B model locally. All new features remain optional.
The Hub owns tasks, routing, policy, signatures, evidence and promotion; Workers
execute closed assignments only.

## Adaptive LLM growth / MoE upcycling

`worker/training/growth/` implements the optional Torch byte-transformer
reference architecture. Attention projections are explicit so width replication
can preserve the function: duplicate channels and heads, divide incoming weight
contributions, replicate LayerNorm parameters, and scale the readout. Depth
insertion zeros residual output projections. Dense-to-MoE upcycling clones
experts, initializes a small seeded router, executes only selected token/expert
pairs and adds a load-balancing loss. Expert utilization/collapse is measured.

AdamW moments and step counters migrate for depth, width, upcycling and pruning.
Second moments use squared scaling. Migration preserves state, not an identical
future optimization trajectory. Unsupported architectures, optimizer classes,
precision and distributed profiles fail explicitly. General Transformers/HF
checkpoint surgery is not claimed. The trainer port allows another supported
implementation without rewriting the experiment.

Safetensors checkpoints contain weights, config, optimizer state, RNG and parent
digest. The existing atomic ResearchCheckpointManager persists optional receipts.
CPU and CUDA are explicit device profiles; CPU is the supported full-offload
fallback. Parameter admission happens before model allocation. One token/FLOP/
time budget covers every arm and repeat; analytical FLOPs are labeled estimates.
Reports include active/total parameters, tokens/second, parameter/optimizer bytes,
process RSS, CUDA allocation, perturbation, bilingual loss, retention and authored
held-out safety/grounding/arithmetic probes. Equal-token and approximately equal
estimated-FLOP comparisons include fixed, grown and scratch baselines. Confidence
intervals and raw rows are retained; synthetic probes do not establish competence.

The additive Research stage `adaptive_growth` uses `LocalResearchBackend`, existing
source admission, assignments, runtime verification, quota/dispatch and result
ingress. Its output kind is `adaptive_growth_report`. Existing Research split
`test` is mapped to the experiment's frozen `holdout`; records accept additive
`language: de|en`. Model config must match the bound recipe. The experimental
stage cannot enable automatic release. Checkpoint/report storage is charged to
the same assignment budget.

The Hub's AdaptiveGrowthPolicy proposes opt-in growth from validation plateau,
gradient bounds, expert utilization, cooldown, change count and remaining budget.
AdaptiveGrowthDecisionService writes a content-free decision with policy/signal
digests; audit failure blocks the proposal. GrowthRegressionPolicy proposes
rollback only with a verified parent checkpoint. A proposal never grants
permission: further execution needs a fresh Hub assignment, not a Worker loop.

```bash
python scripts/adaptive_growth_experiment.py --dry-run
python scripts/adaptive_growth_experiment.py --output /tmp/growth.json \
  --checkpoint-dir /tmp/growth-checkpoints
python scripts/adaptive_growth_experiment.py --device cuda --output /tmp/growth-cuda.json
```

These CLI runs are bounded technical observations. Productive execution uses the
existing Research Hub API/queue and its `adaptive_growth` capability/policy.

Research references: [Net2Net](https://arxiv.org/abs/1511.05641) and
[Sparse Upcycling](https://arxiv.org/abs/2212.05055). The implementations here are
explicit tiny reference transforms, not a reproduction of every paper result.

## Jeff specialist adapter

The `jeff` decision provider composes the existing TypeSafe transport and the
existing Hub cascade. Choice, Score and Noul retain their own semantics. In
particular, Jeff's Score is the raw-distribution expectation and is not
reconstructed from displayed temperature-transformed probabilities. Bad labels,
missing distributions, nonfinite values and missing runtime revision are rejected.
Independent questions are separated, or isolated by the Worker when a signed
whole-request assignment is present. A shared deadline, bounded retries, admission
slots and circuit breaker limit failures. Redirects are prohibited; endpoints
must match the administrative local allowlist. Keys are referenced, not stored
in config or logs.

Upstream [Jeff](https://github.com/logan-markewich/jeff) is pinned to
`34b32f99a727c47b679adde33f4702a001e02979` (declared MIT). The
[GLiFormer model](https://huggingface.co/knowledgator/gliformer-large-v1) is pinned
to `d0a4e53d09cebe6bc963dd9be319d4279084bb2d` (declared Apache-2.0).
The checked manifest includes configuration, tokenizer and weight SHA-256s;
the wrapper rejects replacement inventories. The upstream loader uses
`weights_only=True`. CPU and CUDA dependency locks include wheel hashes. Keep
upstream/model notices when distributing images/assets; Ananta remains AGPL.

`worker/decision/Jeff.Dockerfile` supplies the offline wrapper. No Hub package or
GLiFormer dependency is imported into the Hub. The service has no default public
port. Compose sets an internal network, nonroot user, read-only filesystem,
capability removal and bounded memory/queue. `/healthz` is public inside that
network; other routes require the configured service key. Input byte bounds count
state, instructions and labels conservatively against an encoder-token limit.
The upstream input-echoing validation responses are replaced by fixed errors.

Productive inference requires an Ed25519-signed assignment. Only the Hub's
SpecialistInferenceAssignmentService signs it, after the Registry validates the
reserved run, task, assignment, dispatch lease, exact input and model profile.
Non-test issuance also requires current Hub dispatch-lease authority. The Worker
has only the public key; it validates expiry/input/model, rejects replay by run
binding and limits execution to the grant deadline. Responses echo a digest of
the exact grant for the client to verify. Missing authority returns bounded 403;
it cannot wait for a person. The existing Hub Registry/result-ingress remains the
authority for accepting results and production claims. A signature alone does
not promote evidence or a model.

Set `ANANTA_JEFF_MODEL_DIR`, `ANANTA_JEFF_KEY_FILE` and
`ANANTA_JEFF_HUB_PUBLIC_KEY_FILE` outside the repository. Files must be readable
by container UID 10001. `docker-compose.jeff.yml` is opt-in profile `jeff`;
`docker-compose.jeff-cuda.yml` adds opt-in `jeff-cuda`. `JEFF_REQUIRE_ASSIGNMENT=0`
is an explicit synthetic inference-smoke policy only; its results cannot satisfy
production gates. Normal deployment defaults to mandatory signed assignments.

Hub configuration:

```yaml
decision_providers:
  enabled: true
  providers:
    jeff:
      enabled: true
      api_key_env: JEFF_API_KEY
      base_url: http://jeff:8000
  areas:
    tool_routing:
      mode: shadow
      cascade: [jeff]
```

A task caller attaches the Hub-issued opaque token to DecisionRequest. The
SpecialistDecisionBaseline bridge reuses existing Specialist contracts, parses
closed labels and applies abstention. GLiFormer fine-tuning and GGUF export are
explicitly unsupported; inference can serve as a frozen Model-Factory baseline
without pretending a LoRA/QLoRA backend exists. Promotion/rollback remain in the
existing Specialist/ML-Intern lifecycle and Registry-backed release gates.

## Jev / llama.cpp and Vision

DecisionQuestion and VisionField support `independent`, `sequential`, `grouped`
and `unknown` dependency modes. Existing callers retain their legacy independent
default; new tasks must explicitly declare dependencies when independence cannot
be established. The parallel providers reject dependent/unknown requests before
inference, allowing the Hub cascade's bounded System-2 fallback. The LLM prompt
includes dependencies. Nothing in either model path can override hard policy.

llama.cpp distributions must contain the complete unique allowed value set,
finite values in [0,1] and a normalized sum. Invalid distributions are not repaired.
Scoped batches require the same tenant, subject and schema; admission is bounded.
Batch aggregate usage is charged once. Per-request context/prompt/media caching
is requested off; sharing inside one closed batch is allowed. Diagnostics retain
tree/scored-node/margin/entropy information and full probability distributions.
Raw logits are not exposed by this pinned runtime and are not invented.

Optional `local_decision.scoped_prefix_cache: true` reuses only the static prefix.
Its SHA-256 scope binds tenant, subject, purpose, model and schema. The patched
server replaces the engine and clears media state whenever this scope changes.
Contexts and media remain uncached. The client requires the exact scope/policy
echo; missing tenant/subject or an old server returns a bounded failure. The
recorded A/warm-A/B/warm-B/return-A test proves cache reuse inside a scope and a
cold prefix after every scope switch. Scoped namespaces are accepted only from
the trusted Hub transport; productive deployments must protect the server with
an internal network and service authentication.

The root-tracked patch `scripts/patches/llamacpp-decision-media-isolation.patch`
adds `cache_media` and an effective cache-policy echo to fork revision
`782daeb1f719b24f4567939c2be386f87a2d5d38`. It is additive; the submodule itself
is unchanged. The build helper checks the revision and patch applicability.
Strict local/vision profiles reject old servers that cannot verify cache policy:
`local_decision.require_cache_isolation: true` and
`VISION_DECISION_REQUIRE_CACHE_ISOLATION=1`. Compatibility mode is not proof of
tenant isolation and must not be promoted as such.

```bash
python scripts/build_parallel_decision_runtime.py --build-root /tmp/decision-cpu
python scripts/build_parallel_decision_runtime.py --build-root /tmp/decision-cuda \
  --cuda --cuda-arch 120
python scripts/parallel_decision_profile.py --url http://127.0.0.1:8096 \
  --output /tmp/parallel-profile.json
python scripts/vision_decision_benchmark.py --url http://127.0.0.1:8096 \
  --model Qwen3VL-2B-Instruct-Q8_0.gguf --require-cache-isolation \
  --output /tmp/vision-pilot.json
```

CUDA builds need a toolkit/compiler and the driver's build-time stub link path;
container builds should use a build cache and make copied libraries discoverable
in the final runtime. A driver stub must never shadow the real driver at runtime.

## Evaluation, rollout and remaining evidence

The original CC0 pilot corpora use authored or rendering-oracle labels, not model
labels. DE/EN translations and image variants share groups. Specialist admission,
redaction, group splits, frozen holdout and benchmark classification/safety metrics
are reused. Vision has 120 generated cases, 80 validation/holdout requests, including
rotation, lighting, tiny text, ambiguity and visible injection text. Calibration
fits only validation; insufficient samples produce no usable calibrated profile.
The holdout never selects thresholds. Reports include ECE/Brier, risk/coverage,
ordinal error, false-allow/deny, latency and raw observations. Raw peakedness is
not described as a calibrated correctness probability. Pilot size/quality has
not justified activation: all evaluated profiles stay shadow or experimental.

CPU and RTX 5060 Ti CUDA observations are linked in the four Todo tracks. Those
are synthetic technical observations, not Registry-grounded production release
evidence. The CUDA Jeff observation uses the existing pinned local training image;
it does not certify the separate reproducible CUDA-lock image. Hybrid/recurrent
and real MoE inference model measurements, robust real-world labeled datasets,
full isolated load/OOM/soak profiles, live task-specific calibration and production
rollout evidence remain explicit follow-up acceptance items. No live deployment
or productive promotion was performed.

Architecture review: SRP separates model transforms, training, checkpointing,
evaluation, Hub policy and transport. DIP/ISP use small injected backend, evidence,
lease and audit ports. Compatibility preserves existing facade debt: the large
Vision module still mixes image preparation, parsing and HTTP; TypeSafe transport
still combines retries and usage pricing; the legacy global DecisionService
factory remains. A gradual improvement is to extract those adapters while keeping
the public facades. No new Worker orchestration or Worker-to-Hub service import
was introduced. The repository-wide architecture checker still reports 18 prior
violations in untouched routes/repositories; changed code introduces none.

Default tests are headless with deterministic transport/model doubles. The separate
CPU CI job installs optional execution dependencies and runs actual transformer
training. Local model/container observations are reported separately; missing
hardware or weights are never reported as a passed model test.
