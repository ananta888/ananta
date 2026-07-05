# ananta-codecompass-review

GitHub Actions workflow that builds a local CodeCompass graph (CRG-001
contract + RIG-003 CMake extractor), computes a risk summary (CRG-005)
and posts it as a PR comment.

The workflow is *report-only* by default. ``fail-on-risk`` is an explicit
opt-in and is not the default.

Per CCRIG-DD-009 the workflow does not import or invoke upstream CRG/SPADE
floating-main revisions. It reads only the versioned JSON exports and the
CMake File API reply from a previously run build step.

## Permissions

The default job uses ``contents: read`` only. The PR-comment job uses
``pull-requests: write`` and is gated by a separate ``inputs: opt-in``
input so untrusted PR data never reaches a shell.

## Triggers

The workflow runs on:

- ``pull_request`` (type != forked) for trusted PRs
- ``workflow_dispatch`` for manual runs

Untrusted PRs from forks do not get secrets or write permissions.

## Inputs

- ``fail_on_risk``: optional, default false. If true, exit non-zero when
  ``risk_score`` exceeds ``risk_threshold`` (default 0.7).
- ``risk_threshold``: optional, default 0.7.
- ``post_pr_comment``: optional, default true for trusted PRs.