# ml-intern Fit Assessment (optional specialized worker)

## Assessment scope

Evaluate ml-intern as an optional bounded worker/backend candidate for selected task classes, without changing Ananta control-plane ownership.

## Potential fit areas

- ML-oriented research assistance
- dataset and experiment note synthesis
- domain-specific documentation drafting
- optional code-support for ML pipelines

## Reusable ideas vs direct code reuse

Reusable ideas:

- loop-resilience heuristics as generic patterns
- capability-driven backend selection hints
- explicit review gates for risky automation

Not reusable as-is:

- any internal orchestration logic that assumes worker-owned planning/routing
- worker-autonomous task dispatch chains
- implicit control loops that bypass hub policy and audit contracts

## Why ml-intern must not become a second control plane

Ananta already defines hub-owned queue/routing/governance. A second control plane would:

- split policy authority
- reduce explainability and audit consistency
- create worker-to-worker orchestration paths that violate architecture constraints

Therefore ml-intern can only be integrated as a specialized execution backend behind hub decisions.

## Integration recommendation

Use an adapter boundary with:

- explicit capability profile
- bounded operation classes
- policy + approval enforcement by hub before delegation
- standardized result and trace contract back to hub

This keeps Ananta generic and worker-agnostic while still allowing targeted specialization experiments.
