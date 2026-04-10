# Retrieval Benchmark Scenarios

This benchmark set provides reproducible retrieval scenarios for core Ananta subtask types.

## Scenario Set

- `bugfix-timeout` (`task_kind=bugfix`, `bundle_mode=standard`)
- `refactor-symbol-neighborhood` (`task_kind=refactor`, `bundle_mode=standard`)
- `architecture-overview` (`task_kind=architecture`, `bundle_mode=full`)
- `config-xml-integration` (`task_kind=config`, `bundle_mode=standard`)

## Metrics

`scripts/retrieval_benchmark.py` scores each payload on:

- marker coverage (`expected_markers` hit ratio in `context_text`)
- duplicate rate (from retrieval fusion dedupe counters)
- noise rate (candidate-to-final reduction signal)
- retrieval utilization (budget model utilization)

The aggregate output includes:

- overall average score
- averages by `task_kind`
- per-scenario diagnostic rows

## Usage

Prepare a JSON file keyed by scenario id:

```json
{
  "bugfix-timeout": {"chunks": [], "strategy": {}, "context_text": "", "token_estimate": 0, "budget": {}}
}
```

Run:

```bash
python3 scripts/retrieval_benchmark.py --payload-file /path/to/payloads.json
```

This gives a comparable baseline for ranking/fusion changes over time.
