# Performance Profiling Agent Example Flow

```text
baseline -> profile -> hotspots -> hypothesis -> sandbox patch -> candidate benchmark -> regression -> compare -> report
```

For the demo fixture, the baseline calls a slow Python function. A candidate
patch replaces repeated linear work with a formula. The benchmark is CPU-only,
requires no network and writes no production files.

Possible outcomes:

- `passed`: benchmark improves above threshold and regression passes
- `rejected`: benchmark regresses or output changes
- `inconclusive`: measurement is missing, noisy or below threshold
- `blocked`: policy or approval gate prevents execution
