# CI Flaky Tracking

This project treats recurring unstable tests or workflows as release risk, not as background noise.

## When To Open A Flaky Issue

Open a flaky tracking issue when one of these signals appears:

- The same test or workflow fails and passes again without a code change that explains the difference.
- A failure is repeatedly fixed by rerunning the job.
- A timeout appears in the same area more than once in a release cycle.
- E2E output contains inconsistent navigation, readiness, or browser timing symptoms.

Use the `Flaky test / unstable workflow` issue template and attach the relevant GitHub Actions artifact names.

## Triage Fields

Each flaky issue should record:

- affected workflow and job name
- failing test or command
- first observed commit or date
- artifact names used for diagnosis
- suspected trigger, such as timing, container readiness, network dependency, browser state, or shared test data
- owner for stabilization
- release impact: blocker, watch, or non-blocking

## Release Decision

Before a release tag, open flaky issues must be reviewed. A release may proceed only when each open flaky item has one of these outcomes:

- fixed and verified by a later run
- explicitly accepted as non-blocking with rationale
- promoted to release blocker

This keeps CI reliability visible and preserves trust in the release gate.
