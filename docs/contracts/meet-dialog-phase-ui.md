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
