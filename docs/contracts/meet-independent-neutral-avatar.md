# Independent neutral machine avatar

Implemented neutral-source slice for MAP-12/20/21/24 and companion MDS-11.
The legacy bounded MP4 turn still couples camera, microphone and chat; live
dialog now has an independently controlled neutral avatar alongside speech
and screen. Approved image/profile switching remains separate unfinished work.

## Contract and ownership

The isolated `avatar` source port requires the existing `avatar.publish`
capability and exact verified session, lease and membership generation. Its
only initial profile is explicitly selected `neutral-ai-v1`: an application-
drawn 256×256 canvas, visibly labeled `ANANTA` and a large `KI`, at most five frames per
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

An additional generation-bound controller pulse expires after 2500 ms. Only a
fresh authenticated Hub update causes the Worker to pulse the browser; normal
ticks and pending setup never generate their own renewal. A stalled Hub call
therefore cannot leave a self-animating source alive for the full 30 seconds.
Pulses do not extend the activation or Meet lease, grant capabilities or count
as cryptographic Hub receipts themselves. A brief same-authority rekey pauses
frame requests for at most two seconds; changed membership/lease still closes.

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

## Worker, operator UI and actual integration

`AvatarBrowserPort` starts setup without blocking the Hub exchange loop and
owns a generation-bound local operation token. Late setup completion, rejected
busy opens, stale pulses and stale close cannot acquire another source's
generation. `DialogAvatarPump` owns no renderer or policy: it applies fresh Hub
controls and closes on changed scope, expired state or navigation. New source
activation after expiry requires another Hub update; a graph failure requires
changed authorization state. Meet's separate source watchdog also stops output
while the Worker is blocked. The runtime now supports `avatar.publish`.

The existing UI offers explicit default-off neutral-avatar permission and a
separate activation button after task start. Avatar-only tasks need no chat,
speech or listening. Account/project changes reset the selection. Optional-source
handling preserves independent speech state and legacy API shapes; standard
existing labels/buttons are reused without a new visual framework.

Verification: 23 Worker/contract cases plus existing pumps/speech gave 60 passing
checks in 44.05 s. Actual bridge JavaScript additionally exercised late setup,
busy ownership, stale pulses and oversize results with Node (7.18 s). All 57
Meet UI tests passed, targeted ESLint and Angular template compilation passed
(an unrelated pre-existing RouterLink warning remains).

The private actual Hub/Worker/Meet avatar gate passed in 36.95 s. It verified
default-off admission, real Hub CAS activation, moving decoded KI avatar with
screen, a correlated spoken answer with 220500 locally completed synthetic
samples and 498 non-silent remote observation windows, avatar-only pause,
uninterrupted screen, new-generation resume and parent cancellation. Measured
pause was 31.74 ms locally and 64.73 ms until remote publication removal; those
are observations, not universal latency or remote sample-exactness guarantees.
Capture and transform error counts were zero. No GPU, voice-quality, public
infrastructure or production release evidence is claimed by this tone fixture.

Early failed attempts exposed normal camera downscaling to 64×64 beside screen:
the original small label/thin bar were not readable there. The canvas now uses a
larger KI mark and thicker bar; observations bind to the camera publication and
normalize allowed 64/128/256 decoded sizes, without forcing transport quality.
The failed reports remain failed. One failure's concurrent teardown also hit a
shared-memory SQLite table lock. The fixture now retries only its own terminal
CAS for exact SQLite busy/locked codes, at most three attempts within the
one-second retry budget. Other errors, start/control operations and production
task policy are unchanged. An ExitStack always joins/closes its own test servers
even when cancellation fails. Seven cleanup tests passed (19.60 s), including
a real shared-cache SQLite read lock released before the next terminal attempt.
The ordinary text and new avatar live cases also passed together in 68.97 s
before this final cleanup wiring; the avatar gate with final cleanup then passed
again in 39.44 s. The failure reports remain preserved. Companion integration
and thumbnail fix are committed as `d5c365d`, following source/pulse `2bd7436`.

## Next verification slice: actual GPU voice with independent avatar

Reuse the existing isolated, pinned Qwen/Piper/NVENC fixture for a separately
opted-in `avatar-gpu` case. Preserve its real Worker transport and 20-second
speech profile; the avatar observer must not replace them with the tone double.
Measure actual answer/sample/usage counters, correlated received chat, remote
non-silent voice, moving avatar with screen and real Hub pause/resume/stop.
Classify synthetic policy separately from genuine model/audio execution. This
is still private technical verification, not production release evidence or
generative talking-head quality. No simultaneous full browser matrix or public
service restart is needed for this bounded run.
