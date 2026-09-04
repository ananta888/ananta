# ADR: bounded Python property and symbolic verification

- Status: accepted for bounded pilot
- Date: 2026-09-04

## Context

Ananta has typed contracts, normalization logic, policy decisions, and state
machines that benefit from generated edge cases. Symbolic execution can find
some narrow counterexamples that example tests miss, but it executes analyzed
code and cannot establish general correctness within a finite budget.

## Decision

Use an optional execution-only Worker stack behind tool-neutral contracts and
ports. The Hub remains owner of profiles, scope, budgets, dispatch leases,
evidence identity, result acceptance, and promotion. CrossHair never runs in
the Hub process. Assertions and PEP-316 docstring contracts are the pilot
syntax; adding another contract library is deferred.

Capability decisions after the controlled pilot:

| Capability | Decision | Basis |
|---|---|---|
| Hypothesis core | Go | Ten deterministic properties across contracts, policies, transformations, and normalization; stable shrinking and low fast-suite cost. |
| Hypothesis stateful | Go | Two headless models cover lifecycle terminality and permission monotonicity. |
| CrossHair check | Go, targeted only | Five typed targets complete bounded analysis and the seeded defect yields concrete `-1`. CrossHair remains Alpha. |
| CrossHair cover | Hold/experimental | It emits a concrete Pytest candidate, but usefulness and review cost need more repository-scale evidence. |
| Hypothesis CrossHair backend | Hold/nightly | Five shared properties run successfully, but solver startup is materially slower than the normal backend. |
| CrossHair diffbehavior | Hold/experimental | It finds an intentional semantic delta and reports an equivalent pair only as inconclusive; revision-pair use remains explicitly bounded. |

The local 2026-09-04 pilot ran 39 contract/property/stateful tests in 11.40
seconds and five real-tool tests in 58.96 seconds. Twenty isolated fast-suite
repetitions had no failure, a 0.618-second median, and a 0.854-second maximum.
For the same five properties, normal Hypothesis took 3.406 seconds and the
CrossHair backend 20.853 seconds. These are local technical observations, not
production evidence. The structured report is
`artifacts/test-gates/python-verification-pilot.json`.

The property pilot found and fixed one pre-existing production defect:
self-dependencies were normalized away before graph-cycle detection. Toolchain
tests also found and fixed three integration defects in the new code: a
host-UID-wide process limit, greedy counterexample parsing, and overallocated
per-condition symbolic budgets.

## Security and architecture consequences

The dedicated container has no network, Docker socket, secrets, or host-write
mount. It is non-root, capability-free, read-only, resource-limited, and writes
only into the assigned workspace. Raw tool types do not escape adapters.
Unknown statuses and incomplete bindings fail closed. Synthetic and test runs
cannot produce production evidence.

This split protects SRP (selection, execution, materialization, ingress, and
promotion are separate), DIP/OCP (Hub services depend on contracts and ports,
not tools), ISP (property, contract, and diff runners are focused interfaces),
and LSP (fake and real adapters share closed result semantics). No remaining
known SOLID violation was introduced. The existing in-memory
`AgentRunStateMachine` mixes storage and transition logic; this pilot tests it
without expanding that preserved SRP debt.

## Supply chain

Hypothesis 6.167.1 is MPL-2.0 and requires Python 3.10+. CrossHair 0.0.110 is
MIT with bundled Apache-2.0 and PSF notices and declares Alpha maturity.
hypothesis-crosshair 0.0.30 is MIT. Native Z3 and every resolved dependency are
recorded in the license matrix and hash-pinned Worker lock.

## Rollback

Disable all verification profiles and omit the optional extra/Compose overlay.
Because the default Core and Hub have no tool imports, rollback leaves existing
Pytest and runtime paths unchanged.
