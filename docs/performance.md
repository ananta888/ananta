# Performance und Controlled Parallelism

## Ziel

Dieses Dokument beschreibt den sicheren Betrieb von Parallelitaet in Ananta.

## Messreihenfolge

1. Baseline mit konservativer Concurrency (`1` oder `2`) erfassen.
2. Nur einen Parameter pro Lauf erhoehen.
3. p50/p95 Latenz, Queue-Wartezeit und Fehlerrate vergleichen.
4. Bei Regression sofort auf vorherigen stabilen Wert zurueck.

## Relevante Kennzahlen

- `task_queue_wait_seconds`
- `dispatch_wait_seconds`
- `worker_propose_duration_seconds`
- `llm_call_duration_seconds`
- `strategy_attempt_count`
- `task_success_rate`
- `task_failure_reason_count`
- `workspace_write_conflict_count`

## Operative Grenzen

- Security-Cap bleibt harte Obergrenze.
- Worker-Status `busy` ist kein `offline`.
- Proposal-Budget bricht Strategie-Ketten deterministisch ab (`reason_code=proposal_budget_exhausted`).
- Shared `output_dir` wird standardmaessig gegen parallele Writes gesperrt.

## Empfohlener Startbereich

- Ollama parallel: `1 -> 2 -> 4`
- Proposal-Budget: `max_total_seconds=90`, `max_llm_calls=2`, `max_strategy_attempts=2`
- SGPT backend parallel limits: konservativ pro Backend (`1`) starten.
