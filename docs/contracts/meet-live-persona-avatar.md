# Live persona image avatar

Implementation plan for the remaining MAP-20/21 live image/profile slice.
The independent neutral source and its genuine local GPU voice coexistence
are implemented. Approved profile images currently work only in bounded MP4
turns, not in live dialog source generations. This document is not a claim
that the following steps already work end to end.

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
