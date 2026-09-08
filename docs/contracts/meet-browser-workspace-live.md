# Hub-owned browser workspace and privacy-fenced live view

## Source audit: `e70323868`

MAP-13/15/16 remain open. `BrowserTaskContract.live_view` is a bounded request,
not an assigned workspace or publication grant. Browser-Use and Camofox reject
it honestly. `OwnedDialogScreen` streams only a trusted offline KI status page;
it is not the agent's task browser. The actual Chromium capability/cost probe
and MDS-05 frame transport already exist. The missing work is current Hub
workspace/page authority, real navigation/egress control, privacy fencing and
separate task-bound presentation/control.

## Additive adapter: `public-dom-v1`

Implement an explicitly labelled **sanitized live view**, not an unrestricted
pixel mirror. It uses one new ephemeral task-owned browser context and one
exact Hub-assigned page/navigation epoch. No personal profile, host display,
camera, microphone, persistent cookies or arbitrary existing browser handle
is accepted. Browser-Use/Camofox do not gain capabilities merely because this
separate adapter exists.

The initial profile supports public, unauthenticated, read-only HTTP(S)
documents under exact Hub navigation policy. Scripts, forms, downloads,
WebSockets, secondary pages and embedded active media are unavailable. A
dynamic or authenticated site that cannot operate under that declared profile
returns a bounded unsupported/blocked result; it is not silently upgraded.
Authenticated views need a separate explicit source policy and adapter.

Raw source-page frames never enter the publication queue. A bounded snapshot
is converted to a closed text-block projection, then rendered through
`textContent` in a separate trusted, offline view. Only that view has a CDP
frame producer. Navigation/selection epochs fence pending snapshots and frames.
Source mutation between snapshot inspection and frame delivery cannot smuggle
new source pixels into a previously sanitized frame. This is deliberately
stronger than checking a live DOM and then capturing a concurrently changing
page. The UI must call it a sanitized view, not a pixel-faithful screenshot.

Secret inputs, login/authentication indicators, explicit confidential markers,
unknown canvas/video/cross-origin content or malformed/oversized snapshots
block the view. Content and source URLs are not diagnostic fields. Frame
queues have one bounded latest frame, no screenshot history. Revocation,
navigation, resize, extra-page creation and crash clear pending state before
another publication; no fallback to the raw source page exists.

## Implementation order and verification

1. Separate closed view contract and bounded sanitizer. Deterministic adversarial
   cases cover parser boundaries, unknown content, secret markers, size/depth/
   node/text limits, strict types and non-disclosure in errors.
2. Dedicated public-document fetch/navigation adapter: canonical targets,
   approved public IP connection, redirect/subresource checks, no forwarded
   cookies/auth, explicit byte/time budgets and safe context teardown. Do not
   claim a DNS-rebinding boundary from a preflight DNS check followed by an
   independently resolving browser request.
3. Trusted offline renderer and continuous CDP source under exact workspace,
   page and navigation generation. Actual Chromium tests measure decoded output,
   including secret-marker absence; no raw sensitive frame artifact is stored.
4. Hub-owned ordinary browser Task/dispatch binding, source selection CAS and
   separate navigation/control policy. The publisher executes only the closed
   delegated projection; it cannot create tasks or choose another Worker.
   Existing neutral screen, avatar, speech and v1 clients remain compatible.
5. Connect the admitted source to existing MDS-05 publication, independent
   start/pause/change/stop controls and passive UI. Real receiver tests exercise
   navigation, secrets, role/source revocation, task completion, browser crash
   and other sources surviving under their own current leases.

SRP/DIP: policy admission, bounded snapshot sanitization, public network I/O,
workspace execution and frame delivery remain separate ports/adapters. Do not
expand `OwnedDialogScreen` into a universal browser controller or place Hub
task orchestration in a Worker. Fixed existing source/stop budgets are not
weakened to make tests pass. Each slice is recorded as partial until its actual
composition criteria pass. All tests are headless with explicit synthetic
policy; no production source/run identity is invented from local observations.

## Snapshot and renderer foundation, 2026-09-08

Implemented separate closed `browser_public_view` and immutable
`BrowserViewGeneration` contracts, read-only `PublicDocumentSnapshot`, and
`SanitizedDocumentView` with its own offline trusted page program. No existing
dialog or browser endpoint activates these classes yet. Hub admission, public
network I/O, workspace dispatch and MDS transport composition still follow.

The snapshot walks at most 4096 DOM nodes with ancestor depth at most 48,
retains at most 128 text blocks/8192 characters/32768 UTF-8 bytes, and returns
only bounded text or an empty fixed-reason denial. Forms, secret inputs,
explicit confidential markers, unknown elements and foreign namespaces are
denied. Hidden/off-viewport text and script/style bodies are not copied. This
requires an independently admitted script-disabled public source context; it
is conservative structural filtering, **not semantic DLP or proof that an
arbitrary document is public**. URLs, attributes and action code are not view
fields.

The renderer creates only fixed trusted markup and `textContent` nodes in a
different network-blocked context. It produces continuous 640×360 JPEG frames,
retains only the latest frame for at most one second, and never accepts another
workspace/page/navigation generation. A blocked/malformed view, expiry, resize,
target loss or foreign generation permanently closes that renderer and clears
its pending frame. Source control must obtain a fresh Hub revision before a
replacement instance can be admitted; these classes do not grant that revision.

All **59 focused contract/renderer/browser tests passed in 49.59 s**. The two
actual probes ran in a network-less, read-only non-root container with Chromium
sandbox enabled and exact read-only source-file mounts, not the serving app.
The snapshot probe covered 32 cases. It initially exposed SVG's differently
cased tag name; the fix checks normalized names, namespaces and a closed
ordinary-element set. A second test exposed inconsistent oversized-text reason
classification; the size limit was unchanged and its bounded reason corrected.

The renderer probe decoded eight distinct continuous frames, maximum 10904
bytes, and confirmed source-page magenta pixels never entered the trusted
view. Literal script markup stayed text. Four stop cases covered a secret
input snapshot, resize, closed target and changed generation, with pending
frames discarded. No screenshot file, raw sensitive-image artifact, human
capture, network access or production evidence was produced. This is local
decoded renderer verification, **not yet a decoded Meet-receiver privacy gate**.
The host Chromium attempt reported no usable sandbox; the successful probe
used the existing sandboxed container without disabling the sandbox.

SOLID review: view validation is transport-neutral, DOM reading owns no Task
or publication state, and the renderer owns only its context/frame lifecycle.
The old status-screen and browser-adapter behavior are unchanged. Network and
Hub workspace policy will compose these ports rather than make the renderer a
browser/task orchestrator. MAP-13/15/16 remain in progress.

### Public-fetch implementation boundary

Move the existing pure URL/IP parser to the standalone contracts namespace,
keeping the old Hub import as a compatibility facade. The new read-only fetch
profile uses exact canonical origins, only public addresses and no inherited
proxy/cookie/auth state. Resolve once, reject mixed/private DNS answers, and
connect a numeric socket to the admitted address; HTTPS still verifies the
original hostname/SNI. Redirects, authentication challenges, downloads, unknown
content encodings and malformed/oversized framing are bounded denials.

Execute each network fetch in a separate supervised child with a closed stdin
request, minimal non-secret environment, bounded body/response and an absolute
parent timeout. This also bounds DNS, TLS and trickled response headers;
socket inactivity limits alone are not a whole-request deadline. The child
does not execute page scripts, follow redirects or spawn tasks. The browser
workspace consumes only the validated result under the still-current Hub page
generation. The network fetcher is not a new public endpoint or a grant to
fetch an arbitrary caller URL. Verify target/framing/connect seams and real
timeout/cleanup behavior before wiring it into the workspace.

Public fetch implemented and verified: **247 tests passed in 88.01 s**, including
the unchanged legacy navigation-policy tests. Closed requests admit at most
eight exact canonical HTTP(S) origins on their default ports. DNS is resolved
once; mixed/private/unsupported answers are denied before opening a socket.
The socket connects to the checked numeric IP and TLS retains certificate and
original-hostname verification. Requests are fixed unauthenticated GETs, with
no ambient proxy, cookies, authorization, redirects or retry/fallback route.

The reader bounds status/header lines (1024/4096 bytes), total headers (16384
bytes), identity HTML (524288 bytes) and chunk count (4096). Conflicting or
duplicate framing, downloads, auth challenges, compression and trailers are
denied. Only strict UTF-8 HTML is returned; remote headers/status text are not
returned. The existing bounded process port supplies minimal environment,
bounded stdout, process-group cleanup, cancellation and a three-second maximum
fetch deadline, including DNS/TLS/header stalls. Two tests used actual stalled
DNS children to exercise timeout and revocation; neither performed networking.

An explicit read-only technical probe also retrieved `https://webrtc.ananta.de/`
through this exact default fetch process: 781 UTF-8 bytes in 0.202 s, without
logging content or writing server state. This verifies a real public TLS GET,
not dynamic app execution, arbitrary-site compatibility, Hub source admission
or production evidence. The initial suite's sole failure was an overly narrow
test reason: malformed bare-LF input was correctly rejected by the earlier
line parser. The fixture now accepts either applicable fixed denial code.

SOLID review: URL parsing remains a pure shared contract with a backward-
compatible Hub facade. Target validation, numeric connection, HTTP framing and
supervision have separate responsibilities. The existing generic process port
is reused, preserving its current `voice_runtime.preprocessing` namespace
coupling rather than adding a duplicate process supervisor or a broad unrelated
refactor. The next workspace adapter consumes HTML only after current Hub
assignment validation; no existing endpoint activates the fetcher by itself.

### Hub composition and independent control plan

Add an explicit immutable `browser_workspace: true` dialog option, validated
before dispatch and bound into the original preauthorization/phase identity.
Absent options retain the existing wire and status-screen behavior. This does
not grant network access: a separate default-deny, Hub-owned public-browser
policy restricts tenant/project, owner, exact origins and navigation versus
presentation operations. Existing project/task write access, current parent
role/dispatch and browser task policy checks remain mandatory. Merely joining
a Meet room grants neither navigation nor access to these Hub endpoints.

Each admitted navigation creates an ordinary, short-lived Hub browser Task
with one immutable page/workspace generation, exact parent Task/dispatch/
runtime and inherited organization bindings. The Hub persists the selection
with CAS before delegating it. Workers cannot create or substitute that Task,
extend its deadline, choose a publisher, broaden origins or approve a page.
Navigation and selecting the resulting browser view for presentation are
separate explicit operations; presentation still requires the independent
current screen control and Meet publication lease. A blocked/expired browser
source does not silently select the old status page or another browser.

The installed Worker executes a single admitted fetch asynchronously so DNS
and HTTP cannot stall the control-refresh/media loop. Old in-flight work owns
its slot until cancellation/termination is confirmed; rapid source changes
cannot create an unbounded executor queue. Browser calls remain on the owning
Playwright thread. Only a new authenticated Hub update may consume a fetch
result or activate a source. Fresh current assignment checks surround page
loading, snapshot reading and frame consumption. Parent/child completion,
lease/role/policy revision changes, source failure and explicit stop revoke
the pending frame and the owned workspace independently of other sources.

For this bounded profile, pause/resume of **presentation** retains the same
admitted browser Task/page and does not issue another network fetch. Screen
activation revisions fence the publication wrapper separately. An execution
failure or changed Meet membership/session generation retires that browser
Task on the Worker; it cannot be reloaded under the old identity. Continuing
then requires a new explicit Hub navigation Task (which an authorized headless
workflow may request). There is no implicit fetch retry or source fallback.

Keep new policy, Task persistence/coordinator, projection validation and Worker
presentation in separate modules. The existing dialog composition service has
many responsibilities; preserve only narrow delegation calls there instead
of adding browser policy/SQL/rendering logic to that SRP pressure point. Verify
real SQL CAS and terminality, exact dispatch/tenant/role isolation, bounded
async cancellation, and decoded private Meet receiver privacy/lifecycle cases
before closing MAP-13/15/16 or enabling any public deployment policy.

Workspace composition is implemented as `PublicDocumentWorkspace`, a separate
one-generation adapter accepting only bounded UTF-8 content from an upstream
admitted fetch. Its new context has JavaScript, credentials, downloads,
service workers and network egress unavailable. Source text is inspected again
before each frame is consumed; only changed valid closed snapshots update the
separate renderer. Raw source-page screenshots are never requested. This class
still requires the planned external Hub assignment/current-authority port.

**22 workspace and real-browser tests passed in 104.32 s**: 19 lifecycle seams
plus all three opt-in sandboxed private browser probes. The new workspace
probe decoded seven distinct continuous frames and checked seven stop cases
(secret input, resize, lost page, foreign generation, authority revocation,
extra tab and unassigned navigation). Source scripts did not execute, an
external stylesheet request was aborted, and source magenta pixels were absent
from every decoded output frame. No human input, screenshots on disk, network
access, serving-dist changes or production evidence were involved.

The initial actual test exposed incomplete teardown when a source-tab event
called synchronous Playwright close operations reentrantly. Fixed by immediate
event-side revocation/emptying the renderer buffer, then foreground teardown
before any frame consumption. A dedicated seam regression checks that no
browser RPC is attempted inside that callback and both contexts are closed on
the next foreground authority check. This separates lifecycle invalidation
from resource teardown (SRP); publication never resumes under the revoked
generation. Hub Task and Meet receiver integration remain unfinished.

Hub admission and persistence foundations now pass **199 contract/policy/
negotiation/router/preauthorization/phase tests in 73.18 s** and **40 Task/
negotiation/legacy-source tests in 24.38 s**. The immutable opt-in is preserved
in original dispatch, preauthorization and phase bindings; repository writes
cannot retrofit or remove it. Public browser permissions are explicit
tenant/project/owner/operator-policy rows. Exact public origins remain subject
to the existing browser Task policy, with navigation and presentation checked
independently. No bootstrap or public route activates this coordinator yet.

`HubBrowserTasks` reserves the parent pointer with ordinary Task CAS, ingests a
30-second `meet_browser_workspace` child with inherited scope and exact parent
dispatch/runtime, and checks the persisted child before projecting it. A new
navigation does not present; selection needs its own permission. Conflicts,
policy changes, wrong room, child completion and expiry return an empty source,
never an automatic status-page substitution. New navigation/stop and parent
completion terminate the owned child. Child identity and terminality are
immutable through the normal Task repository. No Worker is allowed to create
that Task or select a different execution destination.

The Task tests found an unsupported `in_progress -> timeout` transition. The
adapter now uses the existing terminal `failed` status with the fixed audit
reason `deadline_exceeded`, without extending or forcing the common state
machine. A separate fixture failure was a missing persisted project required
by actual SQL foreign keys; the synthetic fixture now seeds it explicitly.
There is no human approval, production evidence identity or public activation
in these tests. Worker/MDS composition, crash reconciliation, HTTP/UI controls
and decoded receiver privacy cases remain open.

### Integrated Worker, Hub controls and passive UI

The negotiated browser option now composes `DialogBrowserScreen` in the
ordinary dialog Worker. One bounded fetch slot executes the closed Hub job;
only a fresh authenticated control update may consume its result and create
the task workspace. A separate execution lease checks exact parent/job/Meet
bindings and the unchanged 2.5-second freshness and 30-second lifetime limits.
Presentation wrappers can be closed and recreated without refetching or
destroying the admitted page. Retired task identities cannot reopen. Neither
the Worker timer nor the browser document can create jobs or grant authority.

Bootstrap reads explicit `ANANTA_MEET_BROWSER_PUBLIC_POLICIES` operator rows,
defaulting to an empty deny-all list. Owner-authenticated GET/POST
`/api/meet/v1/projects/<project>/dialogs/<task>/browser` expose bounded metadata
and revision-checked navigation/present/stop/status commands. Responses contain
no document, URL, network endpoint or grant. A signed, exact-child terminal
callback is best effort and nonblocking; the existing Hub deadline sweep
settles expired browser children after Worker/Hub loss. No new orchestration
loop is introduced. `.env.example` and Compose only document/pass this option;
no running operator policy or public installation was changed.

The feature-local Angular control stays passive until explicit user/API
actions. Session negotiation, document loading and presentation are separate
choices. It labels the output as sanitized public text, not a desktop mirror
or semantic DLP. Identity/project/task changes cancel pending UI responses;
uncertain writes are never automatically repeated. The existing shared
explanation component is reused; no browser capture/embed is added to the UI.

Verification before final expanded regressions: **71 backend tests passed in
38.95 s**, **242 Meet UI tests in 2.38 s**, targeted ESLint and Angular
compilation passed (one pre-existing unused RouterLink warning outside Meet).
The real private Hub/Worker/Meet receiver scenario passed in **46.29 s**:
two ordinary browser children/workspaces, no presentation from navigation,
same-page pause/resume with exactly one fetch, received continuous decoded
screen frames, private-input stop in **98.64 ms**, Hub policy-revocation stop
in **1114.95 ms**, chat after source failure, explicit neutral-status recovery
and complete parent/child cleanup. Its document transport and operator policy
are explicitly synthetic. Browser execution, SQL, signed HTTP and Meet media
transport/receiver are real; this is not public fetch, installed-image,
GPU/TURN or production-release evidence. No secret images were written.

The actual receiver test exposed a composition bug missed by pump doubles:
MDS-05 requires `screen:<hub-session-id>`, but the initial wrapper used the
workspace ID. The adapter now preserves the existing session-scoped Meet
source contract and fences its internal page with the separate immutable
workspace/navigation generation. No companion contract or trust check was
weakened. The regression asserts both identities independently. Earlier
generic Hub-unavailable output was teardown following the receiver failure,
not evidence that Hub authority itself was the cause.

SOLID review: the execution lease, document fetch/workspace, presentation
wrapper and terminal reporter own separate lifecycles. Hub policy and SQL
remain behind their existing narrow ports. The already-large dialog fixture
and service are preserved SRP pressure points; new scenario logic lives in
its own module and the fixture uses a shared optional-scenario dispatcher,
rather than extending its main conditional chain. Browser crash, concurrent
SQL ownership and packaged-Worker verification still precede closing the
remaining MAP-13/15/16 criteria.

The final expanded integration regression batch passed **243 tests in
94.21 s**. A separate actual competing-navigation test passed in **18.75 s**
under WAL: both commands reach the same original parent revision, exactly one
ordinary SQL CAS wins, only its child is ingested, and unrelated immutable
parent context remains unchanged. This proves contention for that scoped
navigation path, not multi-node production deployment or public authorization.
