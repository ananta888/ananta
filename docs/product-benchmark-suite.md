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
