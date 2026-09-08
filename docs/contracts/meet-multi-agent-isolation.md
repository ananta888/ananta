# Multi-agent isolation (MAP-28)

## Source audit and execution plan

Baseline: Ananta `36983a7f7`, Meet `c3e378a`. The Hub already derives
assignment-bound machine principals from current Organization role bindings;
`MeetDialogAuthority` revalidates that principal, task, lease, runtime and room.
Source IDs are derived from the admitted session and each browser owns its
device identity. Existing real media acceptance tests cover only one machine
plus a receiver. Their results do not prove two independent machine publishers.

First exercise two separately admitted synthetic machine identities in one
private Meet room, each with its own browser context, task/runtime/session,
persona image, screen and speech. A third, independent receiver must decode
both publishers simultaneously. Check foreign-source rejection and terminate
one publisher without stopping or relabeling the other. Keep required SFrame,
bounded waits, zero human device capture, and test-owned cleanup. Test profiles
are synthetic and a same-host browser test is not multi-container, multi-node,
public TURN or production-release evidence.

Then extend the cross-repository Hub/Worker acceptance path to two independent
Worker containers and authoritative role assignments. The Hub alone creates
and coordinates tasks; Meet owns membership and transport grants. Verify
persona revision/source ownership and independent revocation across both
layers before closing MAP-28. Missing prerequisite acceptance in MAP-12/24/26/27
must not be silently declared complete by this narrower concurrency test.

Preserve the existing broad integration fixture as known SRP debt. Put
multi-publisher observation and scenarios into separate focused helpers rather
than adding another mode to the already large single-dialog test. No policy
or cryptographic bypass is permitted to make this matrix pass.

## Missing Hub routing identified

`HubDialogTasks.start` admits roles against one fixed `publisher_url`, and
`MeetDialogService.start` dispatches through one fixed transport. The existing
role-derived identities therefore do not provide two independently assigned
Worker destinations. Add an optional operator allowlist of at most eight
dialog Worker endpoints. Select exactly one eligible current role assignment
inside the requested tenant/project/Organization/role scope; missing, ambiguous,
malformed or unavailable assignment data must deny instead of falling back.
Persist the selected origin using the existing role binding and revalidate it
immediately before dispatch. Preflight and actual task admission must use the
same selection port. Legacy non-Organization sessions retain their explicit
default Worker. The separate model-generation Worker remains Hub-configured.

Keep selection, bound dispatch and environment parsing separate (SRP/DIP).
No client/Worker-selected endpoint, directory-wide discovery, task rebinding,
retry after an uncertain dispatch, or new orchestration loop. The opt-in
allowlist reuses the existing HMAC trust group; it is not per-Worker credential
isolation, and that limitation must remain explicit. Test two scoped SQL role
assignments, ambiguity/revocation/tampering, exact dispatch, unchanged default
composition, and then the two-container path.

### Operator configuration

Leave `ANANTA_MEET_DIALOG_WORKER_URLS` unset for the unchanged single-Worker
path. To opt in, set a JSON array of explicit private HTTP `/v1/turns`
endpoints on the Hub, for example
`["http://meet-dialog-worker-2:8094/v1/turns"]`. The existing
`ANANTA_MEET_MEDIA_WORKER_URL` remains the legacy default and model-generation
destination; at most eight distinct origins including that default are allowed.
The configured transports retain private-address pinning, no redirects/proxy
fallback, bounded requests, and the existing shared HMAC key. A URL in this
list grants no role: every Organization parent needs exactly one eligible
registered assignment matching its own scope and a configured origin.

Preflight and actual admission independently read those current rows. The
selected origin is persisted in the existing role/Task binding; dispatch does
not select again after a role changes or a Worker fails. Removing a destination
from configuration cannot reroute an already admitted task. Separate
`ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED=1` remains necessary for new
assignment-derived persona identities. No running service is reconfigured by
these source/template changes.

### Implemented routing verification

The separate selector, immutable destination-map router and optional bootstrap
configuration are implemented. Admission and preflight use the same scoped
selection port. Dispatch rechecks current authority, exact assignment fields,
negotiated media flags and the stored role/destination before one send, with
no retry/fallback after an uncertain outcome. Model generation retains its
previous separate Hub-configured Worker.

103 focused checks passed in 72.14 s: real SQL/TaskQueue creation for two role
slots with distinct principal receipts and destinations, independent role
revocation, ambiguous assignments, revocation between selection/admission,
scope/destination tampering, immutable destination map, explicit/default
bootstrap paths, preflight and existing reply budget/role-query regressions.
Execution transports in these routing tests are synthetic doubles, not a
claim of two real Worker containers. The first serial unit run used the much
slower file-backed WAL test profile and was explicitly terminated without a
complete result; verification used the existing isolated in-memory SQL profile.
The actual multi-container/browser gate must use its file-backed test profile.

Selection and dispatch responsibilities are separated (SRP), both use small
ports and injected dependencies (ISP/DIP), and the unset option preserves the
old composition (OCP/LSP). Existing broad Hub task/service composition and the
shared HMAC trust group remain explicitly preserved debt, not newly claimed
isolation properties.
