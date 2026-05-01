# Eclipse Runtime Smoke Checklist

## Purpose

Use this checklist when validating the Eclipse runtime MVP without full Eclipse UI automation.

## Preconditions

1. Runtime status declares `eclipse_plugin=runtime_mvp` in `data/client_surface_runtime_status.json`, scoped to headless/bootstrap evidence.
2. Plugin metadata exists (`plugin.xml`, `META-INF/MANIFEST.MF`, `build.properties`).
3. Build bootstrap command is available:
   - `python3 scripts/build_eclipse_runtime_plugin.py --mode validate`

## Install and startup

1. Validate bootstrap artifacts:
   - `python3 scripts/smoke_eclipse_runtime_bootstrap.py`
2. Validate headless hardening gate when local tooling is available:
   - `python3 scripts/smoke_eclipse_runtime_headless.py`

Expected result:
- Both commands report `*-ok` status.

Known failure symptoms:
- `missing_runtime_files=` when runtime project artifacts are missing.
- `missing_plugin_commands=` or `missing_plugin_handlers=` when command mapping drifted.
- `runtime_headless_tests_failed` when Java runtime test lane is broken.

## Runtime interaction flow

1. Open Eclipse with the runtime plugin project.
2. Configure backend profile and token mode.
3. Trigger analyze/review/custom goal command from current selection.
4. Verify context preview is visible before send.
5. Verify resulting task and artifact references are shown in runtime views.

Expected result:
- Command routing goes through backend client and returns explicit state.
- Task/artifact views show IDs and status fields without implicit mutation.

Known failure symptoms:
- Capability gate blocks command with denied state.
- Policy-denied or auth-failed states are hidden or shown as success.
- Repair-like execution appears without explicit confirmation.

## Notes

- Runtime claim stays at `runtime_mvp`; promotion to `runtime_complete` requires additional automation and packaging evidence.
- Installed Eclipse UI automation is required before claiming more than headless/bootstrap runtime MVP.
- This checklist is non-governance-bypassing: all actions remain backend-authorized.
