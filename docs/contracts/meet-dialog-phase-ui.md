# Optional dialog phase display (MAP-12/29)

Source audit after the private phase browser passed: the Angular dialog component
still displays only Task status. Add a separate focused child component and closed
response validator; do not grow start/retry/control logic with telemetry state.

An explicit read loads the persisted phase; a separate bodyless POST observes
current own publications through the Hub. Neither action joins, grants consent,
enables a source, starts a task or silently retries. Legacy/unavailable phase
endpoints leave the ordinary Task controls usable. No automatic polling is added.

Show revision, last recorded phase and observation timestamp. Label source lists
as registered publications and freshness as freshness at query time, not ongoing
liveness or decoded delivery. A later rendering must not present an old response
as a live assertion. Validate exact keys, task ID, safe integer bounds, terminal
consistency and allowed source names. Reject unknown fields and malformed bodies.

Pending child queries never disable parent Stop. Parent context/control/status,
identity changes and destroy cancel local subscriptions and clear observations;
late responses must not revive an older view. Errors are closed, fixed messages.
Use the existing Hub HTTP/auth port and its bounded request deadline. Verify the
validator matrix, HTTP method/body/task binding, headless component transitions,
parent compatibility and an isolated optimized Angular build. No serving build
or operator configuration is changed. Synthetic tests are not release evidence.

## Verification (2026-09-08)

Implemented as `MeetDialogPhaseComponent` plus a pure closed validator and the
existing Hub API service port. Context, identity, explicit control revision,
status, disabled state and destruction invalidate pending local observations.
Control revision is explicit so synchronous control responses also clear an old
observation. Parent Stop remains usable while the child query is pending.

Final focused Meet/auth regression: 162 frontend tests passed in 1.80 seconds.
The optimized build passed in 23.507 seconds, writing only to the private
`/tmp/ananta-meet-phase-ui-final-build.fBlp7s` output. Existing unrelated Angular
unused-import/CommonJS warnings remain; serving dist was not overwritten.
Freshness is deliberately worded in the past tense, and DatePipe-incompatible
timestamps are rejected. No media-delivery or current-liveness badge is added.
