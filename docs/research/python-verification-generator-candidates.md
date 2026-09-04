# Python verification generator candidates

Date: 2026-09-04

This assessment compares optional test generators with Ananta's existing
Hypothesis and targeted CrossHair path. Generated tests are review candidates
only. They cannot issue Hub evidence identities, satisfy a production gate, or
be promoted automatically.

## Controlled Pynguin benchmark

Pynguin 0.45.0 was installed outside the repository and executed against the
allowlisted production module `agent.services.task_dependency_policy` in the
verification-worker image. The generation container used `--network none`, a
read-only repository and root filesystem, filesystem isolation, seed 42, a
30-second search budget, a two-second per-test timeout, and mutation-analysis
assertion generation.

Observed result:

| Measure | Result |
|---|---:|
| Generation wall time | approximately 42 seconds |
| Reported branch coverage | 60% |
| Generated tests | 13 |
| Original result | 10 passed, 3 strict xfailed |
| Tests with value assertions | 2 |
| Distinct maintainable candidates after review | 1 |
| Unique defects found | 0 |
| Controlled mutants killed | 2 of 3 (66.7%) |
| Review-noise proxy | 3 of 13 strict xfails (23.1%) |

The surviving mutant made dependency normalization always return an empty
list. Most generated cases invoked functions without checking a result, while
several supplied values outside the declared type contract. One killed mutant
was detected only because a strict-xfail candidate unexpectedly stopped
raising. The benchmark therefore does not show unique value over the existing
typed Hypothesis properties and targeted contracts, which already cover
normalization, graph cycles, policy boundaries and shrinking.

Decision: **No-Go for a Core or CI dependency; Hold as an isolated,
review-only legacy test-candidate experiment.** Reconsider only if a broader
benchmark demonstrates unique defects and a materially better mutation score
after human-independent filtering. Pynguin's own project describes it as a
research prototype, warns that it executes the module under test, recommends
isolation, lists Python 3.10 as supported and 3.11 through 3.14 as experimental,
and uses the MIT license:
<https://github.com/se2p/pynguin>. Its assertion documentation explains that
assertions are observations of execution and that mutation-based assertion
generation is available:
<https://pynguin.readthedocs.io/latest/user/assertions.html>.

## Qodo Cover boundary assessment

No Qodo Cover generation run was performed. A comparable network-disabled run
is not possible from the documented open-source path: the tool constructs a
prompt from source, tests and coverage context and calls an external LLM through
LiteLLM. It requires an approved provider credential and a Cobertura report.
The repository contains neither an authorized credential nor an approved data
egress policy for this experiment, and neither may be fabricated.

Additional findings:

- the open-source repository is AGPL-3.0;
- its README has said since 2025-06-15 that the repository is no longer
  maintained;
- its GitHub Action requires write permissions and an LLM API key;
- provider cost, retention and jurisdiction depend on the selected external
  provider, so no universal cost or data-residency claim is possible;
- Qodo's service documentation says relevant code snippets are temporarily sent
  to its servers, even though it also states zero data retention.

Primary sources:

- <https://github.com/qodo-ai/qodo-cover>
- <https://github.com/qodo-ai/qodo-ci/blob/main/README.md>
- <https://docs.qodo.ai/v1/data-sharing>

Decision: **No-Go for Ananta integration.** It has no measured unique finding,
cannot meet the current offline Worker boundary, introduces external-provider
and AGPL review obligations, and the evaluated open-source implementation is
declared unmaintained. A future proposal would require a separate architecture,
security, privacy, provider-cost and license decision plus a fully automated
candidate-review policy. It would still remain outside production evidence.

## Result against the existing stack

Hypothesis remains **Go** for deterministic PR-facing properties. Targeted
CrossHair check remains **Go** for small pure contracts and found the seeded
defect used to validate the toolchain. CrossHair cover and diffbehavior remain
**Hold/experimental**, and the Hypothesis CrossHair backend remains
**Hold/nightly**: the current five-property measurement was 0.576 seconds on
the normal backend versus 14.148 seconds on the solver backend. With no unique
Pynguin or Qodo defect demonstrated, Ananta keeps Hypothesis plus targeted
CrossHair as the maintained path.
