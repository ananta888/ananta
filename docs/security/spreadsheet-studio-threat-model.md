# Spreadsheet Studio threat model

Untrusted assets include workbook archives, formulas, links, embedded objects, macros, model-proposed actions,
Worker results and training projections. Protected assets include tenant documents, immutable versions,
secrets, Hub availability, datasets, adapters and promotion authority.

Current mock-slice controls:

- contracts reject unknown fields, invalid cells, non-finite values, oversized cells/actions and complex ASTs;
- formulas are a closed data AST; URLs, macros, UNO, Python, shell, extensions and free-form formulas are absent;
- hidden-sheet writes, duplicate targets, stale versions and mismatched snapshot digests fail closed;
- the executor returns a content-bound candidate and direct diff but cannot publish it;
- validation and promotion are an atomic Hub transaction with idempotent proposal replay;
- tenant and owner bindings apply to every document API;
- Hub spreadsheet modules cannot import office, document-parser, Worker or ML runtime packages;
- automatic decisions never require or accept a human-in-the-loop bypass.

Residual release blockers are real document archive inspection, zip-bomb handling, formula/recalc fidelity,
process/container isolation, external-link and embedded-object scanning, indirect diffs, retention/erasure,
artifact quotas, multi-Hub storage and all grounded LibreOffice and model-quality evidence.
