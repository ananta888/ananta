# Business Data Quality and Controlling

The controlling subsystem is a read-only Hub workflow. It profiles admitted
CSV/XLSX source revisions, binds a confirmed column mapping, evaluates
deterministic rules and reconciliations, and stores findings as immutable,
version-bound evidence. It never posts, pays, corrects, deletes, or reverses a
business record.

## Architecture

- The Hub owns source admission, mapping, run configuration, policy, runtime
  switches, findings, dispositions, audit, and API responses.
- Workers may execute a delegated bounded analysis. They cannot create another
  task, contact another Worker, or change a source record.
- Rule and reconciliation results are authoritative deterministic categories.
  Optional statistical findings are separate advisory findings and cannot
  override them.
- Explanations render only recorded rule, calculation, or statistical receipt
  evidence. Business-cell text is prefixed and handled literally as data; it
  never changes tools, policy, or workflow control.
- Audit and CodeCompass projections contain IDs, versions, digests, bounded
  classifications, and an optionally redacted record locator. Raw values are
  excluded.

The implementation uses narrow ports between admission/import, statistical
capability admission, analysis, persistence, API, and UI. This preserves SRP
and DIP and keeps the existing historical import/reconciliation services
substitutable.

## Runtime controls

The default state is fully disabled. `scripts/business_controlling_runtime_control.py`
is non-interactive and returns machine-readable JSON. Example rules-only pilot:

```bash
python scripts/business_controlling_runtime_control.py \
  --state-path data/business-controlling-runtime-control.json \
  replace --expected-revision 0 \
  --global-enabled true \
  --statistical-enabled false \
  --explanations-enabled true \
  --actor-id rollout-automation \
  --reason synthetic-rules-only-pilot
```

There are independent global, statistical, explanation, and per-catalog-entry
switches. Disabling a switch blocks new use but never removes historical
evidence.

Statistical execution additionally requires an approved, immutable
Scientific-Skills catalog entry in `controlled-execution` mode, network denied,
the correct tenant/project binding, and an enabled runtime entry. The currently
approved documentation-only pilot entries therefore cannot execute statistics.
No package/model download or external API call occurs implicitly.

## API and workbench

The authenticated `/api/v1/controlling` API provides status, admitted-source
profiling, mapping confirmation, run start, finding list, optimistic-concurrency
disposition, and redacted export operations. The Angular workbench at the
Source Control Center child route `/system/sources/controlling` uses these
server contracts; it performs no browser-side accounting calculation or policy
decision. Tenant authority remains server-side and is not selected by the
browser.

A run can complete fully automatically in deterministic rules-only or admitted
statistical mode. A disposition is optional downstream metadata: an open
finding never blocks test completion and never permits a financial action.
Tests and acceptance gates must not prompt for or wait on human input.

## Release gate

The local synthetic gate covers deterministic rules, known seasonal and
one-off false-positive behavior, tenant isolation, malformed files,
macro/formula denial, policy bypass, provenance tampering, runtime budgets,
global/per-entry switches, rollback, and the invariant of zero automatic
financial actions.

Local success is not production evidence. Production release remains
`release_allowed=false` until actual provided `SRC_*` and `RUN_*` identifiers
are valid and bound. Unknown or missing identifiers are unverified; they are
never generated from filenames, local test names, commits, or fixture IDs.

## Known limits

- The first statistical method is a deterministic robust seasonal residual
  score. It is not a forecast guarantee or an accounting conclusion.
- Mixed currency, grain, incomplete reference windows, unadmitted sources, and
  unavailable runtime control fail closed or become inconclusive.
- External Scientific Skills remain optional. Their outage cannot suppress or
  alter deterministic results.
