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
