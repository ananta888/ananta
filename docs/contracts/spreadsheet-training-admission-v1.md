# Spreadsheet base baseline and training admission

Live Spreadsheet Studio training is fail-closed behind an immutable Hub decision. The decision implements
the accepted requirements associated with `SRC_0003`, `SRC_0005` and `SRC_0014`; it does not create or infer
any additional source identifier.

`ananta.spreadsheet-base-model-baseline.v1` binds one base-model digest to a non-publishing,
execution-backed evaluation report. The quantitative metrics cover strict schema validity, action validity,
safe rejection, execution success, validator pass rate and unintended changes. The report also binds its
sample set, evaluator version, policy, output schema and serializer digests.

`ananta.spreadsheet-training-admission.v1` combines that baseline with the current immutable dataset manifest,
split lock and a digest-bound resource profile. Policy thresholds are copied into the decision so a replay is
auditable and reproducible. Readiness checks include record count, workbook lineage, instruction-template and
leakage-cluster diversity, all four locked splits, exact consent coverage, masking, license policy, task kind,
tenant isolation, supported base model and context capacity.

A `no_go` is a normal automatic outcome. It leaves the base-model-only product path available and cannot be
converted to `go` by a caller. Live Spreadsheet Studio jobs require command V3, the exact admission ID and a
closed training profile; the ML-Intern job contract receives the admission and profile governance digests.
Dry-runs remain available through the legacy command without a training admission. No test or production
transition requires a person.
