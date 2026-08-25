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
resource-checked group if one runtime exits.

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

This is a bounded smoke, not a soak test. Long-context concurrency, repeated
OOM recovery and p95/p99 performance remain operational gates.

## Needle and LFM SFT-LoRA

Training candidates are created with:

```bash
scripts/run-local-adapter-training.sh needle2 dataset.jsonl
scripts/run-local-adapter-training.sh lfm2.5-2.6b-agentic dataset.jsonl
```

Needle training is CPU-only, `nice 15`, restricted to the configured CPU set,
uses a 256-token cap, disables upstream data generation and writes a versioned
adapter candidate. It requires the unquantized Needle checkpoint; the installed
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
shadow and canary samples, and zero shadow side effects. Live violations invoke
the rollback policy. Promotion must use the existing immutable adapter registry
and controlled runtime restart; training never overwrites active weights.
