# E2E Visual Evidence Strategy

## Purpose

This strategy defines how Ananta E2E runs produce **functional** and **human-reviewable** evidence for CLI, TUI, and Web UI flows.

The objective is not only API success, but also usability confidence: reviewers should quickly see if runtime states are clear, actionable, and safe.

## Evidence model

Each E2E flow records:

1. structured status (`passed`, `failed`, `skipped`, `advisory`)
2. logs
3. terminal snapshots (CLI/TUI)
4. screenshots (where UI exists)
5. optional short videos (non-blocking by default)
6. trace references and generated artifact references

Blocking assertions remain functional first. Visual evidence is additional review material and never replaces policy/security checks.

## Required flows and evidence

### CLI

- health / connectivity
- goal submission
- task status visibility
- artifact display
- degraded and policy-denied output states

Required evidence:

- stdout and stderr snapshots for key commands
- normalized output to reduce volatile diffs (IDs, timestamps, local paths)

### TUI

- health/dashboard state
- task and artifact views
- degraded state rendering

Required evidence:

- terminal text snapshots
- optional screenshot or terminal image when available

### Web UI

- dashboard / health
- goals/tasks
- artifact view
- degraded state where applicable

Required evidence (when Web UI is available in the environment):

- screenshots
- optional short demo video

## Blocking vs advisory rules

- **Blocking**:
  - functional failures in golden paths
  - missing required evidence for claimed runtime surfaces
  - redaction failures (secret/token/path leaks in stored evidence)
- **Advisory**:
  - optional video missing
  - non-critical visual diffs where required text/state markers still exist

Reports must separate blocking failures from advisory observations.

## Redaction rules

Before persisting logs, snapshots, metadata, or report snippets:

- redact token-, password-, secret-, and api-key-like values
- redact sensitive local/private absolute paths where practical
- preserve policy/audit facts required for debugging

Redaction must never transform denied/degraded states into misleading success messages.

## Artifact layout

Artifacts are stored under:

`artifacts/e2e/<run_id>/<flow_id>/`

Recommended file naming:

- `stdout.txt`
- `stderr.txt`
- `snapshot-<name>.txt`
- `screenshot-<name>.png`
- `video-<name>.mp4`

Each run emits a JSON report at:

`artifacts/e2e/report.json` (or `artifacts/e2e/<run_id>/report.json` for isolated runs)

## CI and local usage

### Local

- run deterministic mocked harness flows for quick feedback
- optionally enable heavier capture (video/headless UI) explicitly

### CI

- run blocking E2E evidence checks by default
- keep optional video capture disabled unless explicitly enabled
- publish report and key snapshots/screenshots as artifacts

## Extension guidance

Future Blender/FreeCAD/KiCad integrations should reuse:

- the same artifact/report schema
- the same blocking vs advisory separation
- the same redaction-first evidence storage rules
