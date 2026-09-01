# Spreadsheet Studio UX contract

Spreadsheet Studio remains a project-scoped Angular feature backed exclusively by authenticated Hub APIs.
The UI never receives Worker endpoints, storage paths or container-local handles. This decision implements
the reviewed UX dispositions `SSFR-GND-005` and `SSFR-UX-001` through `SSFR-UX-004`, grounded by
`SRC_0004`, `SRC_0008` and governance run `RUN_0001`.

## Visible workbook projection

The immutable imported or published workbook artifact remains the complete source artifact. Interactive cell
display uses `ananta.spreadsheet-workbook-viewport.v1`, projected by the Hub from one exact document version.
The Angular viewer compares the returned snapshot digest with the selected version before enabling mutations.
Historical versions use the additive
`GET /api/spreadsheet-studio/documents/{document_id}/versions/{version}/viewport` endpoint.

One request covers at most 10,000 coordinates, returns at most 250 occupied cells per page, and renders no more
than 24 cell rows in the DOM. Range, sheet or version changes cancel the preceding observable request. Hidden
sheets and unsupported objects remain visible as explicit state; unsupported objects and digest mismatches block
Apply.

## Candidate and promotion states

The page first submits a proposal with `automatic_promotion=false`. It exposes proposal/action digest, base
version and digest, complete or paginated direct/indirect diff, validation outcome, reason codes and unsupported
objects. Promotion repeats the same bounded action and validator set against the same expected version and base
digest with `automatic_promotion=true`; stale state therefore fails in the Hub instead of being silently rebased.
Production execution may return a delegated queue job. The UI polls only the owner-authorized Hub endpoint
`GET /api/spreadsheet-studio/proposal-jobs/{job_id}` until its automatic terminal result; it never contacts the
Worker or waits for a person.

The default mode automatically advances a valid candidate, preserving a fully unattended production path.
Operators may disable automatic advancement to inspect and explicitly apply, edit/re-propose or discard a
candidate. Neither mode bypasses the Hub policy or validation gate, and no test requires a person.

## Accessibility and learning separation

Workbook sheets use an ARIA tab list, the bounded cell projection uses an ARIA grid, status changes use live
regions, failures use alerts, and every control has a visible or programmatic label. Range selection binds the
document ID, version, snapshot digest, sheet and exact cell bounds. The validator builder emits only a closed
validator union and never accepts free-form JSON.

Feedback, masked privacy preview, consent, revocation impact, dataset split lock, training and inference remain
separate visible states. Links to Model Training carry only bounded opaque dataset, job or adapter IDs and reuse
its Hub facade for dataset, evaluation, approval, quarantine and rollback operations.
