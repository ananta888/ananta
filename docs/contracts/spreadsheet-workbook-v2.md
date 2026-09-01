# Spreadsheet workbook V2 and actual diff

`ananta.spreadsheet-workbook-snapshot.v2` is additive; V1 remains accepted unchanged. V2 makes execution
semantics explicit and digestable:

- locale, IANA-style timezone, 1900/1904 date system, recalc profile and engine version;
- stable sheet IDs plus `visible`, `hidden` and `very_hidden` visibility;
- numerically bound row/column coordinates and their canonical A1 address;
- separate raw value, displayed value, formula text and closed formula AST;
- bounded style registry, named ranges, tables, charts, dependencies and unsupported-object reason codes.

All objects are closed and quantitatively bounded. Unknown fields, coordinate/address mismatches, unknown style
references, external formula operations and unsupported objects fail closed. Before Worker dispatch, the Hub
projects V2 to the closed V1 execution view. After execution it merges calculated values and action effects back
into V2, rebases range/table/chart metadata for structural actions, derives dependencies again and validates the
new V2 digest. This projection does not give the Worker ownership of document policy or promotion.

The formula AST additionally supports bounded range aggregates (`AVERAGE`, `MIN`, `MAX`), comparisons, unary
negation and `IF`. It still excludes volatile functions, external links, macros, free-form code and network
functions.

`ananta.spreadsheet-actual-diff.v1` reports deterministic cell, value, formula, style, visibility, named-range,
table, chart, dependency and unsupported-object changes. Each page is limited to 1,000 items. `diff_digest`
binds the complete sorted result, so every page has the same whole-diff identity. Direct cell changes retain
their Hub-issued action IDs; recalculated and object-level consequences remain marked indirect.

`ananta.spreadsheet-workbook-viewport.v1` provides a bounded range/tile projection without truncating the
stored backend snapshot. Ranges are limited to 10,000 coordinates and pages to 1,000 populated cells. Every
page carries the complete snapshot digest, backend cell count, total matching cells and an explicit `has_more`
marker; callers can therefore distinguish paging from backend data loss.
