# Kanban/model dashboard shared contract fixture

`kanban-model-dashboard.v1.json` is the deterministic cross-runtime fixture for
HKM-INT-001. It deliberately contains no timestamps generated at test time,
random identifiers, environment paths, credentials, `SRC_*` identifiers, or
`RUN_*` identifiers.

The minimal fixture binds one board, card, event, revision conflict, and model
summary. The card and event share the same task ID and revision; the event has
a fixed sequence; the error fixes HTTP status `409` and reason code
`kanban_revision_conflict`.

The following tests depend directly on this exact file:

- `tests/test_kanban_model_dashboard_shared_contract.py`
- `tests/client_surfaces/operator_tui/test_dashboard_shared_contract_fixture.py`
- `frontend-angular/src/app/contracts/kanban-model-dashboard.fixture.spec.ts`
- `tests/client_surfaces/operator_tui/test_kanban_cross_surface_e2e.py`
- `frontend-angular/tests/kanban-cross-surface-live-hub.spec.ts`

The last two consumers form one opt-in, bidirectional live-Hub scenario.
Pytest owns a single real Flask/Werkzeug Hub and its deterministic TaskDB state.
Playwright exercises the Angular client in Chromium, while a subprocess probe
uses the production `DashboardHubAdapter` and `DashboardSurfaceController`
paths used by the operator TUI. The probe is deliberately not a terminal
renderer test; rendering, input, and viewport behavior remain covered by the
focused TUI tests.

The Angular test reads this repository file at runtime. No generated or copied
frontend fixture is permitted.
