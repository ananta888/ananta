# SFU broadcast release gates

This document describes the staged release gates for SFU broadcast quality.

- pr: schema consistency, focused Python contracts, lint, Angular unit tests and build, migration-head coverage.
- container_browser: pinned supply-chain and accessibility/browser runtime checks.
- fleet_gate: aggregated evidence from `scripts/run_sfu_broadcast_fleet_gate.py`.
