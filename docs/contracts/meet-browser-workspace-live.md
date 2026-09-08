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
