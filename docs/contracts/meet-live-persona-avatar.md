# Live persona image avatar

MAP-20/21 implementation record. The original plan and intermediate foundation
sections below are historical. Live image selection, signed hydration, Worker
publication and UI are now integrated, with a private cross-repository image
switch/revocation gate. This is not public deployment or production release
evidence; actual profile/catalog permissions also have separate SQL/race tests.

## Boundaries

The Hub selects and revalidates assets, current organization ancestry and
profile digests. The Worker executes only its exact assigned image projection;
Meet checks its own session, publication capability and current source lease.
No worker-to-worker delegation, browser capture, external image URL, asset
generation or new evidence issuer is introduced.

The image avatar renders an approved static image with an unavoidable KI mark
and bounded liveness indicator. It is neither a human camera nor lip-sync.
Image selection does not authorize speech. An explicitly disabled video output
must still prevent publishing an image as a camera track. The legacy combined
MP4 adapter continues to require image, voice and video eligibility.

## Ordered implementation slices

1. Extract a small shared profile/image binding service with immutable required
   outputs. Keep the existing MP4 adapter's public interface and defaults, and
   add an image/video-only avatar adapter through composition. Check real SQL
   profile revisions, organization lifecycle, current asset policy, disabled
   voice independence, disabled video denial and no neutral fallback.
2. Add a closed `persona-image-v1` browser source variant alongside unchanged
   `neutral-ai-v1`. Accept only bounded normalized PNG bytes with an exact
   digest, never URLs or caller labels. Bound decoding, release late bitmaps,
   and preserve camera-only ownership, controller heartbeat, SFrame readiness,
   expiry and generation fencing. Verify real Chromium/Firefox decoding and
   image replacement with simultaneous independent screen and speech.
3. Bind explicit image/profile selection to a current Hub task CAS. Persist
   references and selection pins, never image bytes. Profile changes advance
   the avatar control generation and fence old output before a replacement.
   Hydrate through a separate bounded assignment-authenticated image callback;
   do not expand the ordinary small dialog control envelope to image size.
4. Integrate Worker hydration and source selection with fresh Hub updates.
   Revalidate profile, asset, parent assignment and Meet membership on every
   authorization; missing/revoked selections fail closed without neutral
   substitution. A stale fetch or callback cannot publish into a new control
   generation. Keep the browser watchdog authoritative during blocked I/O.
5. Add explicit UI/API selection without making headless policy-controlled use
   dependent on UI input. Test actual Hub pause/switch/revoke/cancel with remote
   decoded images and continued independent speech/screen, then run the wider
   relevant regression batch.

SRP/DIP: shared profile binding owns selection validation only, adapters declare
their output requirements, rendering owns decoded images, and the Hub task
aggregate owns CAS. Existing dialog service wiring remains a composition root;
do not grow it into a media decoder or asset store. All tests use automatic
bounded synthetic admission; technical results do not mint production evidence.

## Implemented profile foundation

`MeetImageProfileBinding` now owns shared immutable pin validation.
`MeetPersonaProfiles` retains its existing MP4 interface and image/voice/video
requirements. The composed `MeetAvatarProfiles` requires image/video only and
rechecks both current publication policy and profile pin around asset checks.
No live endpoint is enabled by this foundation alone.

Sixteen new tests plus the existing profile/MP4-output suites passed: 41 checks
in 34.89 s. Actual SQL cases cover stale profile pins, organization/project
revocation and durable asset tombstones; deterministic races cover profile
mutation during final asset authorization. One initial test attempted the
invalid organization lifecycle `suspended`; the existing SQL constraint rejected
it correctly. The fixture now uses the supported `archived` state. No database
constraint, permission or legacy MP4 output requirement was relaxed.

## Implemented browser/Worker image primitives

Companion `25d6df3` implements the separately tested `persona-image-v1` port:
bounded normalized PNG/hash/decode, fixed KI label, late-bitmap cleanup and
generation fencing, with real Chromium/Firefox image replacement beside
speech/screen. Its full check passed (568 frontend, 499 Node passed, two skips).
Ananta's additive `AvatarBrowserPort.start_image` verifies the closed tenant/
project-bound assignment, then passes only verified PNG bytes and their digest,
never Hub profile metadata, to this port. Neutral operation stays unchanged.
The receipt validator accepts image mode only when explicitly expected by the
caller; an image receipt never silently satisfies a neutral source control.
These are primitives, not yet enabled dialog image selection or hydration.
The image bridge, actual JavaScript race harness and existing neutral pump
regression passed together: 33 tests in 27.56 s. Stale image completion cannot
replace a later neutral generation, and corrupt or foreign assignments never
allocate a browser operation. An initial test-only import typo was corrected.

The next Hub CAS slice must explicitly negotiate image support for new tasks;
an old neutral-only assignment must reject image selection, not continue showing
the neutral avatar. Persist only mode, immutable reference and profile pin.
Keep image hydration out of the small dialog state envelope and out of task
history. Image-only revocation must quiesce avatar without granting or changing
independent chat, screen or speech controls.

## Implemented passive Hub selection CAS

Closed selection models persist only explicit neutral mode or normalized image
reference plus profile pin. `MeetDialogAvatarSelection` resolves metadata without
reading PNG storage, rechecks the current task around profile/policy lookup, and
uses the ordinary Task aggregate CAS. The persistence port itself requires one
global/one avatar revision increment, unchanged activation and every independent
control, and a non-backwards source timestamp. Concurrent controls, runtime
changes, cancellation, foreign principals and stale selection fail closed.

Existing tasks without a negotiated `avatar_selection` remain unchanged and
cannot use image selection. This internal service is deliberately not exposed
through a productive route before the image-support assignment handshake and
hydration/runtime path are wired. Asset revocation does not implicitly choose
neutral mode. Source policy and closed content-free metadata are separate from
decoding and publication.
The combined CAS/profile/authority/legacy-MP4 regression passed: 95 tests in
69.24 s, including 32 new selection cases and metadata-only profile lookup.
A test originally asserted publication checking was the final asset call; the
subsequent profile recheck correctly also checks preview availability. The test
now asserts publication was checked and no image bytes were read, with existing
publication-denial tests retained.

## Implemented hydration foundation

A separate closed `ananta.meet-avatar-image-request/response.v1` envelope binds
the parent task, dispatch lease, runtime, Hub/Meet session, room, peer, membership,
avatar control revision, exact selection digest and deadline. Requests retain
the existing 16 KiB cap; only this separate response permits at most 8 MiB.
Distinct request/response HMAC domains bind exact response bytes to their exact
request, without changing ordinary dialog or speech protocol limits.

`MeetDialogAvatarImages` re-reads Hub and Meet authority before and after image
hydration. Its small state projection is content-free; a denied image remains
image mode with state `blocked`, not a neutral fallback or unrelated-source
control change. `HubAvatarImageClient` permits only the configured Hub path,
at most six seconds and no redirects, environment proxies or retries. It checks
assignment/reference scope before I/O, authenticates bytes before parsing, then
checks the full binding, immutable reference and PNG hash before returning.

Thirty contract tests passed (24.59 s), sixteen Hub hydration/revocation/race
tests passed (16.27 s), and 23 actual loopback HTTP tests passed (21.26 s).
Transport cases cover wrong nonce/scope/signature/domain/request, oversize,
truncation, compression, redirect, status and deadline. These components remain
unexposed until route/assignment/runtime composition; no productive image
selection or end-to-end Hub image publication was claimed at that foundation stage.

## Integrated live selection and presentation

New tasks may explicitly negotiate `avatar_images: true`, only with the assigned
`avatar.publish` capability and a configured Hub profile authority. They still
start with a paused neutral selection. Old tasks/assignments preserve their exact
wire shape and cannot be silently upgraded. The signed ordinary callback adds
only a content-free avatar projection for negotiated tasks. Both sides reject
missing/unexpected image support rather than guessing compatibility.

Authenticated `PUT /api/meet/v1/projects/<project>/dialogs/<task>/avatar` accepts
only a current `expected_revision` plus a Hub profile pin or explicit
`neutral: true`. Selection and its independent source revision share the actual
Hub Task CAS; selection does not enable a paused source. The separate signed
internal `/api/meet/v1/internal/dialog/avatar-image` callback enforces its own
request/response HMAC domains, immutable assignment binding and byte limits.

The Worker presentation adapter composes the existing source pump with a single
bounded image-fetch slot. No task orchestration, browser access or publication
runs in the HTTP executor. Only a new authenticated control update may consume
the result and activate/pulse a matching generation. Delayed old fetches keep
their sole slot until completion, are discarded after any binding change, and
cannot create an unbounded queue or replace a later selection. Failed hydration
does not retry or choose a neutral image. Ticks only maintain existing sources.

The Angular dialog has an explicit default-off image-support checkbox and a
separate local profile picker. Organization/team/agent IDs resolve through the
existing authenticated profile API; only its validated effective pin is sent
to the selection CAS. The picker fetches no image/video bytes and cannot grant
publication. Voice is independent, while disabled/unsupported visual output
remains unavailable. Edits, account/project changes, pending requests and Hub
changes invalidate candidates. An explicit neutral choice is separate from
failure handling. The Hub rechecks every permission when selecting/publishing.
84 Meet UI tests, targeted ESLint and Angular compilation passed; the pre-existing
unrelated KnowledgeHygienePage unused RouterLink warning remains.

The actual private Hub/Worker/Meet gate passed in 45.66 s: decoded red-to-blue
image replacement during speech, two exact local 220,500-sample completions,
remote non-silent correlated replies, image revocation in 122.79/180.19 ms
locally/remotely, continued screen and a new spoken answer after revocation,
zero human captures and zero transform errors. Images, voice and catalog/policy
are explicitly synthetic, not production approval. Separate actual SQL profile
tests validate real catalog and revocation behavior. The gate exposed and drove
two fixes described in `meet-dialog-speech-browser-batching.md`; earlier failed
runs are not counted as successes.

SRP/DIP: profile policy, passive selection CAS, content hydration, image transport,
source presentation and UI selection remain separate components. Existing large
route/runtime modules remain composition points, not new image-policy engines.
Public deployment, arbitrary-network timing/soak, additional session-renewal
image coverage and the broader MAP-20/21 acceptance work remain tracked separately.

Final compatibility checks: 82 Hub-route/assignment/image-control/Worker tests
passed in 62.22 s. Three additional actual cross-repository cases passed together
in 164.16 s: RTX3080 Qwen/Piper avatar dialogue (52,992 completed local PCM
samples, 105 non-silent remote windows, cold preload 18.86 s), live speech pause
(1.630/1.638 s local/remote) and parent stop (1.713/1.725 s). The GPU case retains
actual-GPU/non-synthetic-audio classification; the interruption fixtures remain
synthetic. No case is production release evidence. Companion `1c6c07f` adds the
fixed read-only color observations; its serial full check passed with 569
frontend and 499 Node successes / zero failures / two Node skips (69.11 s for
the Node matrix), plus explicit external-infrastructure skips.

## Next bounded gate: actual image lease renewal

Before further implementation, extend the isolated cross-repository image
scenario with an explicit 180-second parent assignment. Keep the production
120-second grant and normal early-renewal path unchanged; do not fast-forward
clocks or mint test-only substitute leases. Observe the actual fresh Meet lease
generation in Worker updates and require a new matching image hydration before
the image is visible again. Keep the blue selection across the renewal, then
revoke it and require a new spoken reply plus continued screen without stale
picture or PCM replay. This is a bounded short renewal test, not the separate
two-hour soak or public TURN gate. Existing short/GPU scenarios remain unchanged.

This renewal gate subsequently passed in 92.40 s: actual Meet lease generations
1 and 2, image hydrations under generations 1/1/2, four avatar source generations,
two complete local 220,500-sample replies, no replay after renewal and continued
screen. Subsequent avatar revocation took 1,218.52 ms locally / 1,246.80 ms remotely;
capture and transform errors remained zero. No production clock, lease issuer,
runtime or timeout was changed. Eleven synthetic-catalog/classification tests
passed in 13.18 s, including denial of any silent GPU-to-tone substitution.
