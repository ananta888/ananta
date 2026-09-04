# Ornith 1.5 CodeCompass evaluation

`benchmarks/models/ornith-1.5-codecompass-matrix.v1.json` fixes prompts,
baselines and five deterministic repetitions. The runner sends canonical chat
requests to a loopback-only OpenAI-compatible endpoint. It never executes a
model-proposed tool call and stores response hashes rather than source text.

Run it only inside a Hub-reserved assignment:

```bash
python scripts/benchmark/run_ornith_codecompass.py \
  --matrix benchmarks/models/ornith-1.5-codecompass-matrix.v1.json \
  --endpoint http://127.0.0.1:11434 --model ornith-1.5:9b \
  --output artifacts/ornith-codecompass.json
```

Without `ANANTA_HUB_EVIDENCE_ASSIGNMENT`, output is explicitly `unverified`.
Results may become a routing recommendation only after model, runtime,
quantization, fixture, hardware and Hub run bindings match. Manufacturer
benchmarks remain external references and cannot replace this matrix.
