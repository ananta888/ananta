# Hermes Rollout Checklist

## Default State

- Hermes adapter remains default-off unless release decision explicitly enables it.
- Keep `enable_hermes_worker_adapter=false` for default profiles.

## Required Validation Before Enablement

1. Unit tests:
`tests/test_hermes_worker_adapter_track.py`
2. Security regression:
`tests/test_security_regression.py`
3. Mocked end-to-end flow:
`tests/test_hermes_plan_only_e2e.py`
4. Optional live smoke when endpoint is available:
`tests/test_hermes_live_smoke.py`

## Rollback

1. Disable feature flag: `enable_hermes_worker_adapter=false`
2. Disable adapter config: `hermes_worker_adapter.enabled=false`
3. Remove Hermes from routing candidates and keep native/OpenCode backends active.

## Release Note (Phase 1)

- Hermes is a proposal/review worker only.
- No direct shell execution via Hermes.
- No direct file mutation via Hermes.
- `patch_apply` remains native approval-gated in Ananta.
