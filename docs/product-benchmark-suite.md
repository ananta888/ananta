# Product Benchmark Suite

This suite defines a small fixed benchmark set for Ananta's core product strengths. It is intended for repeatable product evaluation, release retrospectives and later comparative analysis.

## Suite Version

- Version: `v1`
- Catalog builder: `agent.product_benchmark_suite.build_product_benchmark_suite`
- Task count: 5
- Score total: 100

## Fixed Tasks

| ID | Task | Core strength |
| --- | --- | --- |
| `repo-understanding` | Repository verstehen | Goal-to-plan traceability |
| `bugfix-plan` | Bugfix planbar und testbar machen | Reviewable execution plan |
| `compose-diagnostics` | Start/Deploy diagnostizieren | Operations diagnostics |
| `change-review` | Change Review | Governance-visible review |
| `guided-first-run` | Gefuehrte Goal-Erstellung | First-run clarity |

## Criteria

| Criterion | Weight | Meaning |
| --- | ---: | --- |
| Task Success | 20 | Produces a usable result for the stated goal. |
| Time To Signal | 10 | Reaches first useful plan/result quickly. |
| Traceability | 15 | Connects goal, plan, tasks, verification and artifacts. |
| Governance Quality | 20 | Explains review, policy and safe boundaries. |
| Block Quality | 10 | Blocks unsafe or underspecified work with clear reasons and next steps. |
| Result Value | 15 | Leads with user value before internal mechanics. |
| Reproducibility | 10 | Can be repeated with comparable inputs, outputs and evidence. |

## Comparison Rule

Compare only runs with the same task id, profile, governance mode and evidence level. Demo runs, local trial runs and production-like runs should not be mixed.

## Comparative Targets

The suite can be used for contextual comparison against project classes without pretending that all systems optimize for the same product shape.

| Target | Focus | Expected Ananta contrast |
| --- | --- | --- |
| `openhands-like` | autonomous coding, tool execution, developer loop | hub-owned governance, traceability and review signals |
| `opendevin-like` | issue-to-code flow, sandbox execution, iteration speed | task ownership, policy visibility and artifact traceability |
| `openclaw-like` | agent tool use, task execution, local workflow | visible safety boundaries, blocked states and next actions |

## Release Narrative Fields

Release retrospectives and product communication should reuse the same evidence fields:

- `headline`: one-sentence benchmark outcome
- `best_signal`: strongest measured improvement or preserved strength
- `governance_signal`: review, block or safety evidence worth highlighting
- `regression_watch`: weakest benchmark dimension or follow-up risk
- `evidence_links`: CI, artifact, run log or release evidence links

## Evidence Runs

- First local contract validation baseline: `docs/product-benchmark-first-run.md`
- Release evidence slots: `docs/release-evidence-register.md`

## Release Retro Template

```markdown
### Benchmark Evidence

- Headline:
- Best signal:
- Governance signal:
- Regression watch:
- Evidence links:
```
