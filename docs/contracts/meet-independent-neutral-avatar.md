# Independent neutral machine avatar

Implementation slice for MAP-12/20/21/24 and companion MDS-11. The existing
bounded MP4 turn couples camera, microphone and chat; the current live dialog
has separate speech and screen ports but no independent avatar port.

## Contract and ownership

Add an isolated `avatar` source port, requiring the existing `avatar.publish`
capability and exact verified session, lease and membership generation. Its
only initial profile is explicitly selected `neutral-ai-v1`: an application-
drawn 256×256 canvas, visibly labeled `ANANTA / KI`, at most five frames per
second. No caller text, URL, device capture, human browser profile or asset
bytes are accepted. This is a synthetic indicator, not a human camera or
generative talking-head model. A missing approved persona must not silently
select the neutral profile.

Each activation lasts at most 30 seconds, including a bounded 10-second setup.
A 100-ms authority watchdog fences changed membership, lease, capability,
expiry and backwards clocks. Only the concrete protected camera track may
become ready; required-SFrame must retain its existing no-plaintext policy.
Explicit generation-bound close cannot remove a newer activation. No automatic
renewal loop or Hub policy authority is introduced in Meet.

Reuse the camera/microphone ownership allocator: this source owns only camera,
PCM owns only microphone, screen keeps its separate publication. The old MP4
adapter must fail its atomic claim while either occupied slot is held, without
stopping the existing owner. Leave and failed/late setup release only owned
tracks, canvas and timers. Keep source lifecycle, browser rendering adapter and
session-authority projection separate (SRP, ISP, DIP).

## Verification and integration order

1. Companion source/adapter units cover bounds, authority, cancellation,
   ownership, setup failure and stale callbacks; real Chromium/Firefox receivers
   must decode the independent labeled canvas under required-SFrame, with no
   human capture. Independently test coexistence and stop/renewal.
2. Then add explicit default-off Hub avatar controls and a Worker adapter using
   fresh Hub decisions. Do not advertise or enable Worker avatar capability
   before that path exists. Add the operator UI and real Hub lifecycle tests.
3. Approved persona images, mid-session profile switching and optional richer
   rendering remain separate follow-up work with immutable Hub asset bindings.

The existing machine API and conservative capability contract stay compatible.
No productive trust, public deployment, image generation, source/evidence IDs
or release claims are granted by this plan. Validation is fully headless and
uses only private, ephemeral test resources.

## Hub control foundation

The closed optional `avatar` control now uses the same enabled/revision/since
shape as speech, but is initially **disabled even when avatar.publish is
granted**. An explicit current Hub CAS enables the neutral source. It needs no
chat, audio receive or speech permission. Old tasks retain their exact wire
shape; old client updates preserve any assigned optional source unchanged.
Unassigned optional controls are rejected even when supplied as disabled.

A small closed optional-source capability table is shared by contract shape
validation, projection and Hub authority checks, avoiding another series of
speech-specific branches (OCP). Source revisions remain independent; the real
persisted Hub CAS admits a mutation only once. Twelve new cases plus existing
speech/control/authority checks passed (34 in 28.34 s); transport, routes,
task storage and speech output regression passed (33 in 34.81 s).

The Worker capability denylist remains unchanged until the independent browser
adapter and fresh-Hub lifecycle are integrated. This control foundation does
not by itself claim a working end-to-end avatar session or persona switching.
