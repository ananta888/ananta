# Local KAT, LFM and Needle runtime

This deployment is additive. The Hub remains the control plane; KAT and LFM
are model providers and Needle only returns a candidate. A Worker may execute a
Hub-authorized tool, but Needle never creates Tasks or starts Workers.

## Installed deployment

| Runtime | Endpoint/device | Context | Local artifact SHA-256 |
|---|---|---:|---|
| KAT-Coder v2.5 / Colibrì | `127.0.0.1:8082`, CUDA + RAM | 32k request default, 64k server maximum | heat profile `987a1524e33a8c3ea70391275d5d9c782f762a2ccfd8d3935bdb072c5219f160` |
| LFM2.5-2.6B agentic Q8_0 / llama.cpp | `127.0.0.1:8081`, CUDA | 32k; reduce to 16k under pressure | `1e22128dfa128bdfb684da167e74e072d0a056baa7d06d9f280291e2839b0fc9` |
| Needle 2 / cactus-needle | CPU, in-process candidate adapter | 256 | `b43aabfcaf1a6db6acf488076eab71d823c08697c7af4521fc1d174b60ede5ba` |

The hashes identify the files installed on this host. They are not invented
`SRC_*` or `RUN_*` evidence identifiers.

Configure Hub routing with:

```bash
export MODEL_PROFILES_PATH=config/models/local-kat-lfm-needle-rtx3080.model_profiles.yaml
export MODEL_ROUTING_PATH=config/models/local-kat-lfm-needle-rtx3080.model_routing.json
export ANANTA_NEEDLE_WEIGHTS=/home/krusty/moe-test/models/needle2/needle2.cact
```

The corresponding `local_openai_backends` entries are `kat_colibri` at
`http://127.0.0.1:8082/v1` and `lfm_llamacpp` at
`http://127.0.0.1:8081/v1`. Containers must address an explicitly configured
host gateway instead of treating their own `127.0.0.1` as the host runtime.

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
