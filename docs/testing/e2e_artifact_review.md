# E2E Artifact Review Guide

## What gets produced

E2E runs store artifacts under:

`artifacts/e2e/<run_id>/<flow_id>/`

Typical files:

- `flow.log`
- `*.txt` snapshots (CLI/TUI/Web textual evidence)
- `screenshot-*.png` (deterministic placeholder screenshots for mocked mode)
- optional `video-*.cast` artifacts when video capture is enabled
- `report.json` per run
- aggregate reports:
  - `artifacts/e2e/aggregate_report.json`
  - `artifacts/e2e/aggregate_report.md`

## Local commands

Run E2E dogfood checks and generate aggregate report:

```bash
python3 scripts/run_e2e_dogfood_checks.py
```

Run release gate with integrated E2E evidence checks:

```bash
python3 scripts/run_release_gate.py --strict
```

Record optional videos only when explicitly needed:

```bash
python3 scripts/e2e/record_tui_demo.py --enable
python3 scripts/e2e/record_web_demo.py --enable
```

## Review workflow

1. Open `aggregate_report.md` for a compact flow overview.
2. Inspect blocking failures first (`blocking_failed > 0`).
3. Review advisory entries (visual diffs / optional skips).
4. Open the referenced flow snapshots/screenshots for UX verification.
5. Confirm denied/degraded states are rendered as non-success.

## Optional video policy

- Optional videos are **not** required to pass the gate.
- Functional failures and missing required blocking evidence still fail the gate.
- If videos are skipped, the aggregate report keeps this visible as non-blocking.

## Extension guidance (Blender, FreeCAD, KiCad)

New UI integrations should:

- reuse the same report schema (`e2e_report.v1`)
- keep deterministic text snapshots as baseline evidence
- add screenshots/videos as secondary evidence
- preserve redaction and policy visibility rules
