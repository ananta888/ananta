# ADR: Governed spreadsheet transformations

Status: experimental default-off mock slice; research promotion and production release remain blocked.

## Decision

Spreadsheet Studio is a separate Hub-owned bounded context. It uses closed WorkbookSnapshot, structured
Formula AST, Action, Validator, Diff and Proposal contracts. The Hub owns document versions, policy,
optimistic concurrency, idempotency, validation and promotion. Execution sits behind the small
`SpreadsheetExecutionPort`; it cannot orchestrate tasks or publish a document version.

The first executor is a deterministic mock adapter for automatic contract and UI flows. It does not import
LibreOffice, UNO, openpyxl or ML runtimes, does not recalculate formulas and explicitly reports
`production_fidelity: false`. Real LibreOffice work belongs in a separately containerized Worker adapter after
source-grounded sandbox and compatibility research.

## Automation

An admitted proposal can run, validate and atomically promote through Hub policy without a human. Interactive
review remains optional. Tests never wait for approval, consent clicks or other human input. Automatic
promotion is default off and exact-version/digest bound when enabled.

## SOLID boundaries

Contracts, policy, persistence, execution adapter, saga, API and Angular presentation each have one focused
responsibility (SRP). The saga depends on the execution port rather than LibreOffice or the mock (DIP); future
executors extend the port without changing document policy (OCP). Existing artifact and ML-Intern paths are
not modified by this slice.
