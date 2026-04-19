# GitHub Admin Setup

This runbook defines repository settings that cannot be fully enforced by files in the repository. Apply these settings in GitHub after the corresponding pull request is merged.

## Branch Protection For `main`

Protect `main` with these rules:

- require pull requests before merging
- require at least one approving review
- dismiss stale approvals when new commits are pushed
- require review from Code Owners
- require status checks to pass before merging
- require branches to be up to date before merging
- block force pushes
- block branch deletion
- include administrators unless an explicit emergency process is documented

Recommended required checks:

- `backend-checks`
- `backend-smoke`
- `frontend-build`
- `compose-config`
- `release-gate`
- `todo-validation`
- `docs-links`

`e2e-compose`, `architecture-diagrams` and nightly/RC jobs should stay visible but can remain non-required until their runtime and cost are predictable enough for every pull request.

For pull requests that need deeper validation before merge, add the `full-ci` label. That opts the pull request into the heavier Quality And Docs jobs without making them mandatory for every small change.

## Review Rules

Use the repository `CODEOWNERS` file as the source of ownership. For critical areas, enable Code Owner review in branch protection instead of relying on informal reviewer assignment.

Critical areas include:

- `.github/`
- `Dockerfile*`
- `docker-compose*.yml`
- `scripts/release_gate.py`
- `scripts/prepare_release_assets.sh`
- `scripts/generate_release_sbom.py`
- `requirements*.txt`
- `requirements*.lock`
- `frontend-angular/package*.json`
- hub orchestration and worker execution code

## Pull-Request-Only Flow

Normal changes must flow through pull requests. Direct pushes to `main` are reserved for documented emergency repair and must be followed by a normalizing pull request or post-incident note.

Pull requests must include:

- risk and release impact from the PR template
- checks run or an explicit reason they were not run
- CODEOWNERS review where applicable
- release-note label when user-facing, operational or compatibility behavior changes

## Labels

Create or align repository labels with `docs/github-labels-and-milestones.md`. Generated release notes already group changes by these label families through `.github/release.yml`.

## About And Topics

Apply the public repository description and topics from `docs/repository-presentation.md`.

## Admin Verification

Before treating this runbook as active, verify:

1. A test pull request cannot merge while a required check is failing.
2. A CODEOWNERS-owned file requests the expected owner review.
3. A direct push to `main` is rejected for a non-admin maintainer.
4. Label and release-note categories match the repository label list.
