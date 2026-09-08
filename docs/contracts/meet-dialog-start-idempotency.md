# Explicit durable Meet start idempotency (MAP-09)

Source audit at `a4b5dde1e`: Worker dispatch replay is fenced, but each repeated
HTTP start constructs a new task, lease and runtime. An HTTP client retry can
therefore create a second legitimate but unintended participant. Do not infer
that the existing Worker replay fence makes the HTTP operation idempotent.

Add an optional `Idempotency-Key` header to both existing dialog-start routes.
Requests without it retain their exact legacy behavior. A separate Hub start
coordinator rechecks current project/parent write access before looking up any
receipt, hashes the complete tenant/project/owner/parent/key scope, and binds
the key to the exact canonical bounded start payload. SQL uniqueness elects
one caller across Hub processes. Only that caller may invoke the existing Hub
dialog service; the coordinator never delegates to Workers itself.

Completed retries return the original start receipt, not a fresh grant or an
assertion that the original session is still connecting. Current state remains
available through the existing status endpoint. Changed payloads conflict;
pending, failed and uncertain outcomes return bounded machine-readable errors,
never an implicit redispatch. A crash does not release the claim. Durable small
receipts have no automatic expiry/reuse; task/receipt retention policy remains
separate and must not silently turn an old retry into a new session. No raw
idempotency key, media, chat, room invite, grant or exception text is persisted.
These keys/digests are command replay metadata, not SRC/RUN identities.

Use a narrow SQL claim/read/finish port and the existing start/access ports
(SRP/DIP/ISP); leave the broad dialog service unchanged. Preserve role/lifecycle
checks, headless policies, start response and Worker wire. Cover actual parallel
SQL claims, restart replay, payload and scope isolation, current permission
loss, pending/crash/failure/uncertain storage, exact terminal CAS, and optional
HTTP compatibility. Verify one real Hub task/one Worker dispatch for sequential
and concurrent duplicate starts. No trust, serving deployment or production
evidence claim. Persisted lifecycle phases and general recovery remain open.

## Implemented and checked, 2026-09-08

`agent/models/meet_dialog_start.py` owns bounded fingerprints and the closed
receipt. `agent/repositories/meet_dialog_starts.py` owns SQL uniqueness and
one-way token/digest/scope compare-and-set. `agent/services/meet_dialog_starts.py`
composes three narrow ports: current access, receipts and the existing starter.
Bootstrap installs the coordinator; both HTTP routes accept the optional header.
They return the unchanged 202 body plus `Idempotency-Replayed: true` or `false`.
No-header callers retain their previous behavior. A present but invalid header
or missing coordinator never falls back to an unprotected start.

The database persists only two hashes, a winning claim token, outcome and the
original task/session IDs. Completion is historical metadata, not permission to
rejoin. Pending or uncertain execution remains fenced after restart; callers get
a bounded 409/503 instead of interactive approval or automatic retries. Invalid
Unicode and non-finite/oversized JSON are rejected before dispatch.

Verification (local technical observations, synthetic identities/policy/models):

- Initial coordinator/repository/bootstrap tests: 32 passed in 21.74 seconds;
  HTTP and actual Hub queue integration: 48 passed in 29.00 seconds.
- Expanded regression first produced 148 passes and one test-fixture import
  failure in 70.37 seconds: spawned interpreters selected a dependency's `tests`
  package. Explicit checkout import precedence fixed the fixture, not SQL rules.
- Final targeted regression: **150 passed in 67.02 seconds**, including eight
  concurrent connections and two separately spawned processes electing exactly
  one winner, restart replay, cancelled-task non-revival, scope/payload conflicts,
  access revocation, uncertain writes, HTTP compatibility, role lifecycle,
  media-child scope, deadline cleanup and Worker boundaries.
- `MEET_DIALOG_START_GATE=1` private real Hub/Worker/Meet browser gate:
  **1 passed in 39.29 seconds**. Two authenticated Flask HTTP starts with a
  reconstructed coordinator return the same receipt; actual SQL has one dialog
  task and the signed Worker transport dispatches once. The existing receiver
  then verifies moving screen, two correlated chat answers and shutdown.
- Ruff passes on the changed production files and new tests. Six pre-existing
  route formatting findings were corrected without changing route semantics.

The real browser used the private frontend build from Meet `f6d1d90`; companion
source `e0e4bfa` adds test diagnostics/documentation, not a frontend runtime change.
No serving build, trust, operator policy, GPU job or human capture was changed.
The intermittent decoder-start issue was not reproduced by this pass and remains
open. This is not GPU, public TURN, multi-host or production release evidence.

SOLID check: persistence, command coordination and HTTP remain separate (SRP),
the coordinator depends on narrow ports (DIP/ISP), and the legacy start service
is not rewritten (OCP/LSP). Existing broad `MeetDialogService` and cross-repository
test orchestration retain SRP debt; the new browser scenario is isolated in its
own test through one optional fixture hook, without adding a Worker scheduler.
UI callers still need to opt in; complete persistent session phases, recovery,
retention and the remaining MAP-09 acceptance criteria are not claimed complete.
