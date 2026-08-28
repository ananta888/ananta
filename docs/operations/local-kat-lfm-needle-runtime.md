# Local KAT, LFM and Needle runtime

For the complete KAT-Coder-v2.5/Colibrì setup, MoE explanation, CUDA build,
expert-tier sizing and troubleshooting, see
[`kat-coder-v25-colibri-runtime.md`](kat-coder-v25-colibri-runtime.md).

This deployment is additive. The Hub remains the control plane; KAT and LFM
are model providers and Needle only returns a candidate. A Worker may execute a
Hub-authorized tool, but Needle never creates Tasks or starts Workers.

## Installed deployment

| Runtime | Endpoint/device | Context | Local artifact SHA-256 |
|---|---|---:|---|
| KAT-Coder v2.5 / Colibrì | Docker bridge `:8082`, CUDA + RAM | 32k | heat profile `987a1524e33a8c3ea70391275d5d9c782f762a2ccfd8d3935bdb072c5219f160` |
| LFM2.5-2.6B agentic Q8_0 / llama.cpp | Docker bridge `:8081`, CUDA | 32k; reduce to 16k under pressure | `1e22128dfa128bdfb684da167e74e072d0a056baa7d06d9f280291e2839b0fc9` |
| Needle 2 / cactus-needle | Docker bridge `:8083`, CPU sidecar | 256 | `b43aabfcaf1a6db6acf488076eab71d823c08697c7af4521fc1d174b60ede5ba` |

The hashes identify the files installed on this host. They are not invented
`SRC_*` or `RUN_*` evidence identifiers.

Generate local credentials, bind only to the Docker bridge and start the
providers. Never commit or print the generated token:

```bash
export ANANTA_LOCAL_MODEL_API_KEY='<random value with at least 24 characters>'
export ANANTA_NEEDLE_TOKEN="$ANANTA_LOCAL_MODEL_API_KEY"
export ANANTA_LOCAL_MODEL_BIND_HOST=172.17.0.1
scripts/local-multi-model-runtime.sh start
```

Deploy the additive overlay together with the compose files already used by
the instance. It sets profile routing in Hub and Workers, enables Needle in
shadow mode and activates the release-gated central routing editor plus the
read-only legacy picker migration in the Hub. It does not replace existing
LM-Studio or Ollama files:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.base.yml \
  -f docker/compose-next/compose.dev.lmstudio.yml \
  -f docker/compose-next/compose.dev-domain.yml \
  -f docker/compose-next/compose.local-kat-lfm-needle.yml up -d
```

Containers address `host.docker.internal`; no container treats its own
`127.0.0.1` as the host runtime. LFM, KAT and Needle require bearer auth, and
Colibrì additionally restricts the accepted Host header.

With `ANANTA_AI_SNAKE_PROFILE_ROUTING=true`, `/snake/ask` and ordinary Snake
room replies delegate inference to a Worker through the existing
`/step/propose` boundary. The Hub classifies lightweight explanation/intent as
`classification` (LFM) and code, debugging or repository questions as the
corresponding heavy task kind (KAT). Before the main-model call, the Worker
records a Needle decision only when Tiny Router mode is `shadow`; that
candidate has no execution path. An explicit `model` in a `/snake/ask` request
retains the legacy override behavior for API compatibility.

Das lokale Hub-Routing ordnet außerdem die Consumer-Standardrollen direkt
realen Profilen zu: `coder`, `planner`, `reviewer` und `reasoning` verwenden
KAT; `chat` und `summarizer` verwenden LFM. Die `any`-Regel fällt auf LFM
zurück. Dadurch zeigen geerbte Routen wie `chat.code_help` und `task.coding`
kein synthetisches `_global_master_default`-Profil, sondern das tatsächlich
ausführbare lokale Profil. Explizite zentrale Assignments und engere Scopes
haben weiterhin Vorrang.

## Start, inspect and recover

```bash
scripts/local-multi-model-runtime.sh preflight
scripts/local-multi-model-runtime.sh start
scripts/local-multi-model-runtime.sh status
scripts/local-multi-model-runtime.sh stop
```

LFM starts first. The measured shared default uses a 4 GiB KAT expert budget:
the requested 5 GiB left only 921 MiB free, below the mandatory safety reserve.
With 4 GiB, readiness used 7,933 MiB and left 1,945 MiB free. The script uses
`COLI_GPUS=0`, a fixed `CUDA_EXPERT_GB`, the installed heat file and full
256-expert-per-layer RAM residency required by the Qwen3.6 CUDA tier.
The local operator caps KAT's expert-cache RAM at 40 GiB by default; the Hub's
54-GiB process-tree budget also covers the measured 30k-context model and
engine overhead. LFM's measured process tree stays below its 1-GiB Hub budget.
A restart
decision may count only the declared share of resources currently owned by
those managed runtime process trees as reclaimable.

Logs and PID files are runtime-only under `data/local-model-runtime/`. A failed
readiness check prints the bounded log tail and stops; it does not enter an
unbounded restart loop. On memory pressure, stop both providers, retry with
`ANANTA_LFM_CTX=16384`, and only then lower `ANANTA_KAT_EXPERT_GB`.

For session-independent operation install the supplied user service. User
lingering must be enabled so the runtime survives logout:

```bash
systemctl --user enable --now ananta-local-model-runtime.service
systemctl --user status ananta-local-model-runtime.service
```

The service supervises all three child processes and restarts the complete,
resource-checked group if one runtime exits. systemd permits at most three
failed starts in fifteen minutes; repeated crashes therefore end fail-closed
instead of creating an infinite restart loop.

The Hub control bridge is a separate authenticated user service. Containers
cannot reach a host process bound to loopback, so bind it to the explicit
Docker bridge address and restrict that port to the Docker network in the host
firewall. The control token belongs to the Hub only; the Compose overlay does
not expose it to either Worker:

```bash
install -Dm 644 deploy/systemd/ananta-local-model-runtime.service \
  "$HOME/.config/systemd/user/ananta-local-model-runtime.service"
install -Dm 644 deploy/systemd/ananta-local-model-control.service \
  "$HOME/.config/systemd/user/ananta-local-model-control.service"
install -m 600 /dev/null data/local-model-runtime/runtime.env
install -m 600 /dev/null data/local-model-runtime/control.env
# runtime.env contains only provider runtime settings and model/Needle tokens.
# control.env contains only ANANTA_LOCAL_MODEL_CONTROL_BIND_HOST and the
# independently generated ANANTA_LOCAL_MODEL_CONTROL_TOKEN.
systemctl --user daemon-reload
systemctl --user enable --now ananta-local-model-control.service
```

`GET /models/local-runtime/v1/status` separates health from readiness and
reports effective context, declared budgets and measured per-process RAM/VRAM.
`budget_status=unmeasured` is explicit when the platform cannot attribute a
resource; zero is never presented as a successful measurement. The runtimes
report `timeout_supported=true` and `cancellation_supported=true`: cancellation
is a Hub/Worker result fence, so a late provider response or Needle candidate is
never consumed or executed. It does not claim that an inference already accepted
by the model server stopped consuming compute before its deadline. Invocation
telemetry contains only model/profile IDs, stable outcomes, latency, token or
prompt-size counts, fallback index, Confidence availability and the correlated
resource snapshot. It excludes prompts and tool arguments.

## Measured evidence on this RTX 3080

The earlier exclusive tests measured KAT at about 12.14 tok/s with an 8 GiB
expert tier, LFM at about 177.5 tok/s, and Needle at about 433 tok/s with a
reported 133.7 MB peak. These are single-model baselines.

The shared smoke on 2026-08-23 ran both HTTP generations concurrently with a
Needle CPU proposal:

- peak VRAM: 7,933 MiB; remaining reserve after readiness: 1,945 MiB;
- KAT: 42 completion tokens in 4.365 s end-to-end;
- LFM: 48 completion tokens in 0.382 s end-to-end; a following isolated timing
  reported 177.28 tok/s;
- Needle: 0.144 s, 351 decode tok/s and 56.4 MB reported peak RAM;
- Colibrì confirmed an active 4 GiB CUDA tier with 2,421 resident experts.

The bounded smoke above is distinct from the real long-context gate. On
2026-08-27, two parallel KAT/LFM/Needle cycles with 30,016 KAT and 30,011 LFM
prompt tokens ran for 9,709.963 seconds without a provider failure, OOM, crash
or additional restart. KAT TTFT was 4,388,996 ms p50 and 5,297,604 ms p95/p99,
decode throughput 2.532 p50 and 2.996 p95/p99 tok/s, and request-scoped CUDA
expert hit rate 60.8%. LFM TTFT was 27.936 ms p50 and 3,288.411 ms p95/p99,
with 157.153 p50 and 159.834 p95/p99 tok/s. Needle latency was 29.450 ms p50
and 157.296 ms p95/p99. Across 1,874 resource samples, peak process-tree RSS
was 56,341,143,552 bytes for KAT, 753,410,048 for LFM and 58,961,920 for
Needle; peak VRAM was 8,324,644,864 bytes and minimum free VRAM remained
2,033,188,864 bytes.

The real parallel gate is intentionally long-running and writes its volatile
report to the ignored top level of `artifacts/`:

```bash
scripts/local-model-runtime-soak.py \
  --duration-seconds 1800 \
  --minimum-samples 2 \
  --prompt-chars 60000 \
  --minimum-prompt-tokens 30000
```

It refuses durations below thirty minutes, correlates per-runtime latency,
TTFT/throughput and complete process-tree RSS with GPU samples, and fails when
KAT expert hitrate cannot be measured, either GPU provider processes fewer
than the configured long-context token floor, fewer than two parallel samples
complete, or the 1.5-GiB VRAM reserve is crossed. Build the pinned Colibrì
runtime telemetry extension with `scripts/build-colibri-qwen36-runtime.sh`
while the runtime service is stopped; the script restores the external source
tree after producing the local binary.

## Needle and LFM SFT-LoRA

The immutable source of truth for both training bases is
`config/models/local-adapter-training-bases.v1.json`. It binds every required
file by relative path, byte size and SHA-256, plus a canonical snapshot-tree
digest. The catalog deliberately distinguishes training bases from the active
serving artifacts:

- Needle uses `Cactus-Compute/needle2` revision
  `98fbd955b0347e78059be0c253cc1ffa09b87bc7`, the unquantized
  `checkpoints/needle2.pkl`, its matching tokenizer, and `cactus-needle==2.0.9`
  (Apache-2.0). The serving `.cact` is bound separately to the same upstream
  revision.
- LFM uses the post-trained agentic `LiquidAI/LFM2.5-2.6B` revision
  `654f9463ce32b05d0429d76fe1f580b27d4c1ac0` (LFM-1.0), never the `-Base`
  repository. Its two Safetensors shards, index, tokenizer and chat template
  are all pinned. The Q8_0 GGUF remains a separately hashed serving baseline.

Verify local copies and run the bounded load-only smokes with networking forced
offline:

```bash
python scripts/verify_local_adapter_training_bases.py \
  --needle-root "${ANANTA_NEEDLE_TRAINING_MODEL_DIR}" \
  --needle-python "${ANANTA_NEEDLE_TRAINING_PYTHON}" \
  --lfm-root "${ANANTA_LFM_TRAINING_MODEL_DIR}" \
  --lfm-python "${ANANTA_LFM_TRAINING_PYTHON}"
```

The LFM smoke validates every indexed Safetensors tensor without materializing
the full model, loads the exact local tokenizer and chat template, constructs
the model on the meta device, and attaches a Rank-8 PEFT adapter. Transformers
4.57 uses the Worker-owned local tokenizer compatibility seam for the upstream
`TokenizersBackend` metadata introduced by Transformers 5; it neither mutates
the snapshot nor permits remote code or a network fallback.

Training request drafts are created with a catalog-owned immutable Dataset ID:

```bash
export ANANTA_TRAINING_SOURCE_IDS='<Hub-provided SRC_* ID>'
export ANANTA_TRAINING_RUN_IDS='<Hub-provided RUN_* ID>'
export ANANTA_NEEDLE_BASE_MODEL_ID='<pinned unquantized checkpoint catalog ID>'
scripts/run-local-adapter-training.sh needle2 ds-0123456789abcdef0123456789abcdef

export ANANTA_LFM_SFT_BASE_MODEL_ID='<pinned agentic Transformers snapshot ID>'
scripts/run-local-adapter-training.sh lfm2.5-2.6b-agentic ds-0123456789abcdef0123456789abcdef
```

The script never starts training. It creates a closed Hub request whose
`release_target` is immutable lineage. Once submitted through the authenticated
ML-intern API, Needle training is delegated to an isolated Worker, is CPU-only,
uses `nice 15`, two to four configured CPU cores, a 256-token cap and disables
generation. It requires the unquantized Needle checkpoint; the installed
`.cact` is a merged inference blob and cannot be used as the training base.

LFM training is delegated through the existing Hub-owned LoRA job system. The
installed Q8_0 GGUF remains the serving baseline but is not a PEFT training
checkpoint. Set an exact Transformers snapshot, its tree hash, model catalog
ID, registered dataset ID and verified `SRC_*`/`RUN_*` IDs. The script refuses
to invent provenance and only creates a Hub request draft; it cannot dispatch
a Worker or approve its own result.

Candidates must pass `LocalAdapterPromotionPolicy`: perfect JSON/schema/tool
validity, no accuracy regression, per-slice limits, deterministic safety and
resource gates, independent confidence calibration, at least the configured
shadow and canary samples, minimum Shadow match/Canary accuracy, bounded Canary
error, escalation and latency, and zero shadow side effects. Live violations
invoke the rollback policy. Promotion must use the existing immutable adapter
registry and controlled runtime restart; training never overwrites active weights.
The generic adapter approval endpoint rejects both governed local release
targets. Only the Hub lifecycle may atomically promote them after revalidating
offline, Shadow, Canary and policy digests; a failed runtime restart triggers a
compensating Registry rollback and a base-runtime restart.

Automatic weight activation remains blocked until both serving-compatible,
non-executable adapter formats exist. Needle's current finetune CLI emits a
pickle artifact, which the artifact-security service correctly rejects; the
installed LFM GGUF cannot consume an unconverted PEFT directory. Neither
restriction may be bypassed by allowing pickle or claiming a restart selected
weights that were not safely materialized.
