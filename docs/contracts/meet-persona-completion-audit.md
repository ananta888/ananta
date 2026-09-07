# Persona rendering acceptance audit

This is a repository implementation audit, not production release evidence.
Existing local technical observations are not retroactively promoted to SRC/RUN
identities. The full MAP track remains partial.

## MAP-21: static persona image as an independent video track

The historical `partial/75` reason was missing live image selection, hydration,
switching and renewal. Those components now exist. The four task criteria map
to the following source and acceptance coverage:

| Criterion | Implemented boundary and verification |
| --- | --- |
| Approved image or explicit neutral KI source | `MeetAvatarProfiles` rechecks current image/video permission and profile pin; `DialogAvatarPresentation` consumes only the current authenticated projection. Meet renders `neutral-ai-v1` or verified `persona-image-v1` on its own 256×256, five-FPS canvas with a fixed KI mark. No automatic neutral fallback on denial. |
| Appearance does not confer identity or role | `AvatarBrowserPort.start_image` decodes the tenant/project-bound assignment first and sends only normalized PNG bytes/hash. No name, role, organization, URL or grant enters the artwork port. Meet's independent machine session and camera ownership remain authoritative. |
| Replacement, size normalization and revocation | Meet's image loader checks PNG structure/hash/dimensions, scales the artwork inside a reserved area that cannot cover the KI mark, and releases late decoded bitmaps. Python tests cover stale selection, role/project/asset revocation and old hydration results. Private browser gates cover remote red/blue replacement, lease renewal and avatar removal. |
| Independent avatar and screen | Avatar ownership claims only camera; screen uses its separate source/pump. The shared real Hub/Worker/Meet gate keeps screen and spoken replies running through image replacement and avatar revocation. |

Sources inspected include `agent/services/meet_avatar_profiles.py`,
`worker/meet_media/avatar_browser.py`, `dialog_avatar_presentation.py`, and Meet's
`machine-avatar-image.ts`, `machine-avatar-surface.ts` and source controller.
Detailed previous results are in [live persona avatar](meet-live-persona-avatar.md).
The current session operation regression also passed actual image renewal with
spoken replies. All 148 current avatar/profile/hydration/format/TTS regressions
passed in 58.15 s. After both media-scheduling changes, the real image-renewal
and parent-stop cases passed together in 123.90 s against Meet source `c20f453`
and its existing local build. MAP-21 is therefore `done/100` for these four
criteria, not for the entire persona/session/production track. Historical
intermediate notes remain as history and are superseded by the completion note.

SRP/ISP/DIP: profile authority, private image hydration, browser transport and
artwork ownership are separate. Existing runtime composition and test-fixture
coordination remain preserved responsibilities; this status audit adds no
decoder or policy logic to them.

## Explicit remaining work

MAP-20 retains live video/profile switching and multi-session isolation criteria.
MAP-22 retains the pending selected-voice GPU acceptance even though the fixed
Piper port and real default-voice delivery have previously passed. MAP-11/24/28
retain multi-renewal failure/recovery, scheduling/budget and multiple-participant
requirements. MAP-30/31 retain real hardware, public TURN and long-session gates.
None is closed merely because the static-image feature is complete. Current
free VRAM was 1464 MiB, below the existing 4096-MiB selected-voice gate; unrelated
GPU workloads were not modified and no failing mandatory run was relabeled as
a success or synthetic substitute.
