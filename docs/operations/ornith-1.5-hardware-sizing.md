# Ornith 1.5 local hardware sizing

The source of truth is the pair of profiles in `config/runtime/`. Estimates are
admission budgets, not measured performance.

The 9B Q4_K_M candidate starts at 8K on a 10 GiB RTX 3080. 16K and 32K are
separate candidates; 256K is stress-only. The 35B-A3B Q4_K_M profile requires a
64 GB host class and GPU/CPU offload, starts at 8K, and treats 32K and 256K as
stress-only. Both profiles reserve at least 15 percent, allow one concurrent
request and reject swap growth.

Measure a 30-minute profile with:

```bash
python scripts/benchmark/ornith_resource_profile.py \
  --model ornith-1.5:9b --duration-seconds 1800 \
  --output artifacts/ornith-9b-resource.json
```

The probe records RAM, swap, GPU memory, temperature and response timing. It
terminates on swap growth, excessive temperature, timeout or malformed output.
`benchmarks/models/ornith-1.5-hardware-results.v1.json` remains `not_run` until
a real Hub-bound execution completes.
