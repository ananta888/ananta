# Model Intelligence acceptance

## Profiles

The Core profile covers:

- wire and execution contracts
- safe snapshot admission
- truthful capabilities
- static analysis
- model graph
- Hub and worker execution
- artifact and report contracts
- REST/CLI security boundaries

The Extended profile adds:

- bounded local trace capture
- LoRA delta analysis
- evaluation and comparison
- Angular workflow checks when its toolchain is available

## Local commands

```bash
bash scripts/run-model-intelligence-acceptance.sh core
bash scripts/run-model-intelligence-acceptance.sh extended
```

The scripts run offline and write structured output below
`artifacts/test-gates/model-intelligence/`.

For a release-grounded run, provide real identifiers from the authoritative
source and run registries:

```bash
MODEL_INTELLIGENCE_SOURCE_ID=SRC_provided \
MODEL_INTELLIGENCE_RUN_ID=RUN_provided \
MODEL_INTELLIGENCE_CONTAINER_DIGEST=sha256:... \
MODEL_INTELLIGENCE_REQUIRE_RELEASE_EVIDENCE=1 \
bash scripts/run-model-intelligence-acceptance.sh core
```

Do not reuse the CodeCompass repository-analysis gate. Its subject, schemas and
source authority are different.

## Result semantics

`passed` requires successful tests and valid provided evidence identifiers.

`unverified` means technical tests passed but required grounding is missing.

`failed` means tests, schemas or evidence verification failed.

A waiver may document accepted risk, but it must not mutate `failed`,
`unsupported`, `not_run` or `unverified` into `passed`.

## External production gate

Application code cannot complete the production gate. It requires:

- immutable release and report digests
- verified Core gate evidence
- license approval for the concrete model and fixture scope
- security approval for the deployed runtime profile
- a named approver and documented exceptions

This gate blocks `release_allowed`, not implementation work.
