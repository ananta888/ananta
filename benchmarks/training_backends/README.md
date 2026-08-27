# Training backend benchmark

The benchmark compares candidate adapters with PEFT/TRL and Unsloth using the
same admitted model and dataset snapshots where trainer semantics allow it.
Differences in packing, templates, optimizers or checkpoint behavior must be
recorded rather than hidden.

CI validates the contract only. A `verified` result requires a real NVIDIA
run, exact container digest, hardware attestation and immutable model,
dataset, configuration and output hashes. Missing evidence stays `not_run` or
`blocked`; it is never replaced by README or vendor benchmark numbers.

Example validation:

```bash
python scripts/run_training_backend_benchmark.py \
  --result artifacts/test-gates/training-backend-result.json
```

The result file must already contain measurements produced by the isolated
worker. This command validates and classifies; it does not invent a training
run or download a model.
