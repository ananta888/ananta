# Spreadsheet LoRA/QLoRA training profile

The implementation closes the accepted requirements associated with `SSFR-GND-003`, `SSFR-ML-001` and
`SSFR-ML-003`. Their supplied evidence references are `SRC_0001` through `SRC_0008` for the fit analysis,
and `SRC_0003`, `SRC_0005`, `SRC_0014` plus `RUN_0001` for the task-family and runtime decisions. No new
source or run identifier is inferred.

`ananta.spreadsheet-training-profile.v1` is a closed Hub-side contract. It binds the admitted base model,
backend, GPU profile, dataset recipe and split lock to the action schema, serializer, policy and resource
digests. It also bounds quantization, LoRA rank and modules, sequence and cell budgets, deterministic seed,
training schedule, checkpoint cadence and automatic resume policy. The profile digest covers every field
except itself.

Live Spreadsheet Studio training accepts only command V3. The Hub verifies the quantitative admission and
profile before reading or projecting the dataset. It derives the existing ML-Intern command from the profile;
callers cannot separately override backend, model, method or hyperparameters. Generic legacy training and
Spreadsheet Studio dry-runs remain additive and backward compatible.

The isolated worker receives only the derived training configuration and an opaque closed governance object.
That object contains digests for the profile, admitted base model, dataset manifest and artifact, recipe,
split, action schema, serializer, policy, resource profile and admission. The worker independently verifies
its aggregate digest, matches the base-model digest against its local catalog and persists the bindings in
the training manifest. Dataset rows and cell content remain confined to the dataset files and are excluded
from Hub read models, worker metadata and progress telemetry.

Capability selection remains Hub-owned. Mock/CPU execution is fully automatic, while unavailable optional
NVIDIA capability is a machine-readable unavailable/not-run outcome. Existing automatic gates cover worker
capability probing, cancellation, timeout, OOM, checkpoint/resume, artifact integrity and cleanup; none asks
for human intervention.
