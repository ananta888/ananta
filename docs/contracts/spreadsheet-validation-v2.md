# Spreadsheet validation V2

Spreadsheet Studio keeps the V1 proposal shape compatible and extends its closed validator union additively.
The Hub validates and normalizes every rule before delegation; Workers do not select validators, resolve
references or decide promotion.

The union supports exact values, numeric ranges and emptiness as before, plus numeric tolerances with explicit
rounding, relative or exact formula-AST patterns, range invariants, bounded sums, typed range rules,
change-scope rules and tenant-bound reference ranges. Formula ASTs remain closed: macros, UNO, external links,
network functions and free-form code are not validator inputs.

`ananta.spreadsheet-validation-reference.v1` is an immutable snapshot copy. Its digest binds the tenant digest,
owner, source document/version, snapshot schema and snapshot digest. Repository lookup is always tenant-scoped;
cross-tenant references are indistinguishable from missing references. A missing reference produces the fully
automatic `not_verifiable` result and never asks a person to continue.

`ananta.spreadsheet-validation-result.v2` binds these seven digests:

- document/version identity;
- candidate snapshot;
- Hub task/proposal;
- execution engine and version;
- locale, timezone, date system and recalc profile;
- Hub policy;
- normalized validator specification.

The result separately reports technical validity, correctness (`correct`, `partially_correct`, `incorrect` or
`not_verifiable`), change classification (`unchanged`, `expected` or `unexpected`) and safety. Its final outcome
also distinguishes `unsafe`, `unexpectedly_changed` and `unchanged`. Numeric comparisons use declared absolute
or relative tolerances after deterministic decimal rounding. Relative formulas compare cell/range offsets from
their declared origins. Error strings, indirect actual-diff changes and reference formulas are evaluated without
locale-dependent display parsing; locale and date semantics are instead bound into the recalc digest.
Change-scope validation always receives the complete Hub-side actual-diff set. A paginated or otherwise
incomplete diff is classified `not_verifiable` and cannot promote a candidate.
