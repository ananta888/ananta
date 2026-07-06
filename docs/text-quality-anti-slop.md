# Text quality and anti-slop evaluation

Ananta evaluates writing quality; it does not attempt to prove authorship or
hide AI use. A low external detector score is not a success criterion.

The flow is:

1. classify the content kind;
2. run the offline core scanner;
3. optionally collect sandboxed provider and LLM-judge signals;
4. fuse versioned, comparable signals;
5. persist a bounded evaluation;
6. map approved reason codes to fixed prompt rules;
7. propose a disabled prompt version for review;
8. compare a canary only with the same criteria/evaluator/content profile.

Structured plans are not evaluated as prose. Planning integration evaluates
task titles and descriptions as `planning_task_description`. Missing,
degraded and unscorable results are excluded from averages rather than
treated as zero.

## Grounding

Specificity only improves depth when supported by supplied evidence or marked
as opinion/hypothesis. Only supplied `SRC_*` and `RUN_*` identifiers are
accepted. Unknown identifiers yield `source_unverified` and
`unsupported_specific_claim`.

## Privacy and safety

Inputs are bounded and are not persisted as full text. Criteria provenance
stores SHA-256, a short preview and optional authorized artifact references.
External Node execution uses `SandboxBackend`, no network, no shell and a
pinned detector checksum. Rewrite is proposal-only; this subsystem has no
file-apply path.

Safe defaults keep all text-quality evaluation, LLM judgment, external
detectors and evolution disabled until configured.
