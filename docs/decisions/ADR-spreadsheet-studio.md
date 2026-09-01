# ADR: Governed spreadsheet transformations

Status: default-off; durable queue-backed production execution plane implemented, release gates remain open.

## Decision

Spreadsheet Studio is a separate Hub-owned bounded context. It uses closed WorkbookSnapshot, structured
Formula AST, Action, Validator, Diff and Proposal contracts. The Hub owns document versions, policy,
optimistic concurrency, idempotency, validation and promotion. Execution sits behind the small
`SpreadsheetExecutionPort`; it cannot orchestrate tasks or publish a document version.

The deterministic mock adapter remains available for automatic contract and UI flows. It does not import
LibreOffice, UNO, openpyxl or ML runtimes, does not recalculate formulas and explicitly reports
`production_fidelity: false`. Production proposals are persisted in a Hub-owned queue, projected into the
central WorkerJob/slot-lease control plane and claimed by a separately containerized LibreOffice Worker. The
Worker executes only the exact assignment and cannot route, queue, promote or orchestrate work. The Hub alone
validates the callback and atomically promotes a new document version.

## Automation

An admitted proposal can run, validate and atomically promote through Hub policy without a human. Interactive
review remains optional. Tests never wait for approval, consent clicks or other human input. Automatic
promotion is default off and exact-version/digest bound when enabled.

## SOLID boundaries

Contracts, policy, persistence, queue, lease control, capability issuance, execution, result ingress and
presentation each have one focused responsibility (SRP). Hub services depend on narrow repository, WorkerJob,
lease and execution ports rather than LibreOffice or transport details (DIP). The mock and queue-backed modes
remain substitutable at the proposal API boundary while their synchronous/asynchronous HTTP status is explicit
(LSP), and additional executors can extend the control plane without changing document policy (OCP).
