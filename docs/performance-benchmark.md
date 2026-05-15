# Controlled Concurrency Benchmark

## Zweck

Dieses Benchmark-Skript misst Tick-Latenzen fuer Concurrency-Tiers `1/2/4` und erzeugt JSON + Markdown Summary.

## Ausfuehrung

```bash
ANANTA_BASE_URL=http://localhost:5000 python scripts/benchmark_concurrency.py --samples 8 --tiers 1,2,4
```

## Outputs

- `artifacts/benchmark_concurrency.json`
- `artifacts/benchmark_concurrency.md`

Optional wird bei verfuegbarem `nvidia-smi` ein VRAM/GPU Snapshot in den Report aufgenommen.

## Interpretation

- Erhoehe Parallelitaet nur, wenn p95 stabil bleibt.
- Bei OOM/Retry-Anstieg zuerst `OLLAMA_NUM_PARALLEL` reduzieren.
