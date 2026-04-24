# Eclipse Plugin Runtime Bootstrap

This document tracks the runtime bootstrap phase for the Eclipse advanced control surface.

## Scope

Runtime bootstrap covers:

- plugin project layout
- plugin metadata (`plugin.xml`, `META-INF/MANIFEST.MF`, `build.properties`)
- deterministic dockerized Gradle build command
- Java backend client core, secure profile/token handling, and capability gate

It does **not** yet claim full runtime command/view delivery for all advanced screens.

## Build Command

Validate bootstrap build prerequisites:

`python3 scripts/build_eclipse_runtime_plugin.py --mode validate`

Run dockerized Gradle build:

`python3 scripts/build_eclipse_runtime_plugin.py --mode build`

## Smoke Command

`python3 scripts/smoke_eclipse_runtime_bootstrap.py`

## Security and Governance Notes

- Token values are excluded from profile persistence maps.
- Inline secret-like strings are redacted before exposure.
- Capability and permission gates fail closed for unknown or unauthorized actions.
- Eclipse remains a thin client; governance and approval decisions stay backend-owned.
