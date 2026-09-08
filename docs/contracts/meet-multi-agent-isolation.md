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

## Two packaged Worker acceptance fixture

The separate opt-in `tests/test_meet_multi_worker_containers.py` now starts two
immutable-image Worker containers on the private Meet fixture network. Each
uses its own SQL role assignment, signed Hub dispatch, browser process/profile
and registered Meet machine principal. A third browser attributes moving
screen frames to those exact principals. Cancelling the first Hub task must
remove its publication while the second remains active; cancelling the second
must leave the receiver alone with zero device captures or transform errors.

Infrastructure setup, test-only certificate/closed failure observation, and
the scenario remain separate from production authority. Containers run nonroot
with a read-only root, explicit CPU/RAM/PID limits and an independent lifetime
limit. No Hub package, Worker source, GPU, host profile or display is mounted.
Readiness uses the packaged Docker health probe: the production `/healthz`
route intentionally remains loopback-only. The first fixture incorrectly tried
to reach it remotely and was corrected without relaxing that boundary.

One actual run passed in 40.74 s with image
`sha256:3444cb7d1124c52959be70043b4c66fa78bba0e55f109c18b724d2ab46dddb9e`.
This is a single-host screen/lifecycle observation under synthetic policy,
not two-container audio/persona or production-release evidence. Repeated runs
also exposed a control-exchange failure in one Worker; therefore
that first successful run did not establish reliable multi-Worker operation. The
fixture preserves closed failure codes, task states and numeric receiver
counters before its bounded cleanup, never grants, keys or media contents.
No MAP-28 completion claim follows from this result.

### Reproduced fixture capacity failure

The private TLS forwarder was capped at 16 concurrent connections, which the
additional independent browser processes can exhaust. A real failing run
recorded one explicit Node `drop` event, a Hub `ConnectionResetError` (errno 104),
`meet_authorization_unavailable`, and the second Worker's bounded termination.
The same path also produced TLS EOF errors. This was not a role-policy denial
or an SFrame decoder failure. A passing repeat without that diagnosis was not
considered a fix.

Only the new two-Worker fixture opts into a fixed 32-connection profile; the
existing 16-connection default, private network, memory/CPU limits, TLS trust,
Hub policy and freshness deadlines remain unchanged. A saturated, content-free
counter retains at most eight drop events; successful acceptance now requires
zero drops. Six proxy configuration/diagnostic tests passed, and the first
actual corrected two-container runs passed in 41.83 s and 41.27 s. The temporary global
HTTP diagnostic wrapper was removed after isolating the cause; closed Worker
and service-specific failure observations remain. All 21 container/closed
diagnostic unit checks passed in 21.90 s. The companion complete regression
check passed in a separate worktree of `e7c2344`, including the concurrently
pushed packager updates through `5e002cf`: 665 frontend and 752 Node tests,
zero failures, two Node skips, build/security/Go unit+vet green, 241.028 s
for Node. Fourteen external live gates remain explicitly skipped. Companion
results are recorded in `dacc96a`. Earlier unrelated single-Worker/browser startup intermittence remains
separate and is not claimed fixed by this larger private fixture profile.

## Next acceptance: two-container persona and audio sources

Extend the existing packaged-Worker gate with a separate media scenario helper,
not another inference or orchestration loop in a Worker. Both signed assignments
negotiate image avatars and speech/chat; the Hub independently selects a red
and blue test-only image revision and activates each source through its existing
CAS controls. The receiver correlates images, screens and decoded audio to the
same two authenticated principals and actual peer connections.

Use explicitly synthetic bounded PCM generation through the ordinary Hub child
task/reply ports. This test isolates real packaged publication from model speed;
the real RTX/Qwen/Piper path retains its separate GPU acceptance. Start with the
second Hub chat control paused. Automatically consent both receiver-owned chat
paths, observe the first actual spoken reply, respect the existing ten-second
room cooldown, then enable the second chat control and issue its new input
while the first longer bounded reply is still playing. Require audio from both
publishers in one observation window without changing admission budgets.

Revoke only the first admitted image in the synthetic catalog; require that
avatar to disappear while the second image and both screen sources remain
correctly attributed. The existing real task cancellation then proves separate
participant termination. Reject wrong profile references, scope and revisions
in the fixture unit checks. No raw images, PCM, subjects, grants or keys go into
the report; the matrix remains synthetic-policy/single-host, not a production
release, GPU concurrency or Hub speaker-fairness claim. Keep default screen-only
acceptance unchanged and run both cases plus targeted helper regressions.

### Implemented media scenario and readiness corrections

The media scenario now uses separate image-revocation, bounded tone-execution
and scenario helpers. It passed with two real packaged Workers in 69.90 s:
distinct red/blue persona images, two real Hub child replies, three consecutive
fresh observations of simultaneous decoded audio, independent first-image
revocation and the existing separate task cancellation boundaries. The tone
inference and profile catalog remain explicitly synthetic; this is not a GPU
or production release result. Forty-nine Python helper/regression checks passed
in 37.07 s and five companion bridge unit checks passed in 0.527 s.

Two fixture errors were isolated: chat sent before the Worker's real chat port
opened is correctly not replayed; selecting another UI target can leave a stale
checked DOM field while its model is reset. A private passive Worker marker now
records only open/revision values after the unchanged native chat update; the
Hub fixture waits for an exact fresh exchange match. Receiver consent explicitly
changes the checkbox and verifies the selected peer's confirmed grant. No
security decision, event replay, human input or production Worker patch is
introduced. The follow-up combined run passed screen-only but failed during
consent in the media case (78.04 s total), so that single green media run does
not close MAP-28. A closed command-step diagnostic is being used to isolate the
remaining error before declaring the combined matrix complete.

That diagnostic reproduced the failure at the second consent checkbox, before
any Worker failure: Playwright input could race Angular's target-reset render.
The later membership-loss report was teardown, not its cause. Consent now
waits for the selected target's rendered pressed state and reset unchecked
field before dispatching another input event. This is a bounded DOM observation,
not a delay, application state mutation, grant retry or relaxed admission.

The corrected combined packaged matrix passed both cases in 103.64 s:
42.234 s screen-only and 61.045 s persona/speech including fixture lifecycle.
Both reports retain exact image revision and synthetic/single-host classification;
the media case records two child replies, three simultaneous audio observations
and individual image withdrawal. No owned Worker container remained afterwards.

SRP separates observation, synthetic inference/catalog and Hub scenario; DIP
retains the existing real task/reply ports. The large pre-existing integration
fixture still owns composition and cleanup; its broad setup is preserved test
debt, not a new production service responsibility.

## MAP-28 criterion closure

| Criterion | Verified implementation |
| --- | --- |
| Separate participants, personas, sources, keys and withdrawal | Two installed Worker containers, separate browser/device identities and authenticated role principals; red/blue Hub-pinned images, independently decoded screens/audio and individual image/task withdrawal. Required SFrame and membership fences remain enabled. |
| Hub-owned team coordination | Real SQL role assignments and Hub TaskQueue admission select two fixed configured destinations; scoped router revalidates assignment and dispatch exactly once. No Worker schedules another Worker. |
| No display-name or foreign-source identity substitution | Real Hub/Meet principal interoperability retains distinct identities under the same fixed KI label and rejects cross-subject inspection/renewal; both browser engines reject the other session's screen, speech and avatar source IDs. |
| Two agents plus receiver with simultaneous browser/audio | Corrected two-container matrix passed both cases in 103.64 s; six observer/helper checks plus Chromium/Firefox foreign-source/simultaneous-media cases passed in 8.327 s. Explicitly a single-host, synthetic-inference/policy observation. |

These four MAP-28 criteria are satisfied. This does not finish its broader
prerequisite tracks: arbitrary browser-workspace presentation/privacy,
speaker fairness, receive/injection matrix, GPU concurrency, public TURN and
soak/release acceptance remain open. Shared Worker HMAC trust is still not
per-Worker credential isolation; no claim extends beyond the actual admitted
publisher and browser/device/source boundaries above.

Companion `b0d6c10`/`346afa5` and root `6a81636d9` contain this slice. The first
companion full check passed 665 frontend tests/build/static/security, then
failed a separate existing native-packager key-only startup fixture before its
stimulus. Two of ten isolated repetitions also failed. That regression is
tracked and being diagnosed, not hidden by MAP-28 closure or asserted green.
