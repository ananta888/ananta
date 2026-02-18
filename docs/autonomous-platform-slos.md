# Autonomous Platform SLOs

## Service Objectives
- Task ingest latency p95 < 2s
- Claim/assignment latency p95 < 3s
- Build/lint/test gate completion p95 < 15m
- Failed-task auto-retry success rate >= 60%

## Product Metrics
- End-to-end autonomous completion rate
- Human intervention ratio
- Delegation throughput per worker
- Routing correctness by task type

## Reporting
Expose metrics in dashboard read-model and trace endpoints.
