# Eclipse Plugin Runtime Bootstrap

This document tracks the runtime bootstrap and first operational runtime phase for the Eclipse advanced control surface.

## Scope

Runtime bootstrap and operations currently cover:

- plugin project layout
- plugin metadata (`plugin.xml`, `META-INF/MANIFEST.MF`, `build.properties`)
- deterministic dockerized Gradle build command
- Java backend client core using existing generic Hub routes, secure profile/token handling, and capability gate
- runtime command registry and Eclipse `AbstractHandler` command adapters (`analyze`, `review`, `patch`, `new_project`, `evolve_project`)
- bounded workspace/editor context capture with user-review preview
- runtime goal submission panel model with task/artifact result links
- runtime views registry plus Eclipse `ViewPart` adapter classes for task/artifact/approval/audit/repair/TUI/policy-fallback surfaces
- Java unit/integration/security/contract tests for runtime hardening
- Python Hub route contract tests that prevent the Java client from drifting back to non-existent Eclipse-specific backend paths

Runtime implementation includes test/CI hardening and merge-readiness evidence for the current delivery scope. The current claim is **headless/bootstrap runtime MVP**; promotion beyond this requires installed Eclipse UI automation evidence.

## Build Command

Validate bootstrap build prerequisites:

`python3 scripts/build_eclipse_runtime_plugin.py --mode validate`

Run dockerized Gradle build:

`python3 scripts/build_eclipse_runtime_plugin.py --mode build`

If Docker credential helpers are misconfigured in WSL-like environments:

`ANANTA_DOCKER_CLEAN_PATH=1 python3 scripts/build_eclipse_runtime_plugin.py --mode build`

## Smoke Command

`python3 scripts/smoke_eclipse_runtime_bootstrap.py`

Headless hardening smoke (runs runtime bootstrap smoke + Java runtime tests + audit gate):

`python3 scripts/smoke_eclipse_runtime_headless.py`

Operator smoke checklist:

`docs/eclipse-runtime-smoke-checklist.md`

## Security and Governance Notes

- Token values are excluded from profile persistence maps.
- Inline secret-like strings are redacted before exposure.
- Capability and permission gates fail closed for unknown or unauthorized actions.
- Eclipse remains a thin client; governance and approval decisions stay backend-owned.
- Policy-denied states and browser fallback links are explicit and do not bypass backend governance.
- Goal submission uses the Hub-compatible `goal` payload field and existing generic goal/task/artifact/system routes.
