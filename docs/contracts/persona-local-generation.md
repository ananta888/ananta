# Bounded local procedural persona generation

## Plan after source audit (2026-09-09)

Source `591a0f91f` provides verified generated-output admission but no native
generator that emits its receipt. Existing `worker/meet_media/avatar.py`
already establishes a visibly synthetic procedural avatar rather than a claim
of photorealistic likeness or lip synthesis. Reuse that product direction for
an explicit `procedural-avatar-v1` asset generator, not an implicit model or
remote-provider fallback.

Provide one fixed 256x256 PNG or a two-second, 12-fps silent H.264 MP4, with a
closed three-color palette and a visible AI label. This asset-generation
profile intentionally uses CPU/Pillow/FFmpeg in a small separate container;
the existing RTX3080 Piper/NVENC live-response profile remains unchanged.
No uploaded likeness, prompt, URL, shell option, font path, audio recording or
cloning is accepted. Generated clips remain independent of separately
authorized live TTS; they are not lip-sync claims.

The Hub checks explicit current project MANAGE authority and registered license
inputs, admits the immutable recipe source, then reserves a run **before** a
normal queue-owned generation Task is dispatched. Only its closed recipe,
scope, deadline and Registry assignment projection go to the Worker. A
read-only signed callback rechecks the exact Task, lease, evidence and current
project membership; it never extends or creates work. Generation has a fixed
20-second assignment bound, bounded subprocess output and durable single-flight
replay protection. The Worker owns no Hub storage, publication credentials or
task-routing loop.

The Hub checks the returned bytes, creates the exact output manifest, completes
the Task/run and feeds the verified source admission adapter. A separate Hub
service composes explicit image/video policy installation and the existing
delegated inspection/artifact lifecycle. Default purposes are inspect/store/
preview; publication requires an explicit request under the same management
authority. The source/receipt stage grants nothing by itself. Test classification
can never become production evidence. Failure/revocation returns a bounded
machine-readable error, never a human wait or automatic cloud/codec fallback.

Keep pure wire validation, rendering, supervised execution, task persistence,
authority, transport and asset composition in small separate modules (SRP/DIP).
Reuse the existing bounded signed HTTP server/pipe runner where their contracts
match; do not pretend generation is an image inspection task. Preserve legacy
inspection routes, domains and executor behavior.

Verify hostile recipe/assignment/result mutations, exact replay/deadline and
policy cancellation, actual SQL queue/Registry and isolated real PNG/MP4
generation, then the full HTTP Hub/generation-Worker/inspection-Worker/storage
chain. Container limits and cleanup must be asserted. No public service,
operator trust, human device, unrelated GPU job or production release gate is
modified. MAP-18 stays open until this native path is verified.

## Implemented component chain

The optional Hub flag is `ANANTA_PERSONA_GENERATION_ENABLED=1`; configure the
generator URL/key and immutable repository/profile/environment bindings in
`docker-compose.persona-generation-hub.yml`. This does not enable image/video
inspection or publication policy implicitly. Enable the desired existing
inspection Worker separately. The generation container profile is non-root,
read-only, internal-network-only, 256 MiB / 1 CPU / 32 PIDs, with no GPU,
models, host ports, host devices or publication credentials.

`POST /api/persona-media/v1/projects/<project>/generated-assets` accepts only
`{"request": {...}}`, where the request contains `recipe`, registered `license`
pin and `classification` (`synthetic` or `test_only`). Recipe fields are exactly
`profile: procedural-avatar-v1`, `media_kind: image|video`, and
`palette: indigo|teal|amber`. Optional `publish` defaults to false;
`valid_for_seconds` defaults to 900 and is bounded to 60–3600. Only current
project management authority may request this policy. No user text or likeness
is accepted. The returned active asset has already passed a **second**, separate
Hub inspection Task and immutable storage admission.

The generation assignment binds its request **and recipe source pin**. Live
callbacks revalidate both; the terminal SQL compare-and-set also rejects a
changed verification snapshot. This prevents a policy change between the last
callback and completion from turning into a successful output receipt.
Generation and inspection have distinct run IDs and dispatch leases, and a
source fact alone still grants no publication rights.

Component checks: 71 Hub/receipt tests passed in 37.52 seconds, 43 Worker/
renderer/supervision checks in 25.17 seconds (including real PNG generation,
fixed FFmpeg encoding and re-decoding of exactly 24 frames / 2,000 ms), nine
HTTP/asset/native-chain cases in 97.72 seconds, 47 bootstrap/legacy checks in
26.49 seconds and 76 generation-API/inspection regressions in 38.52 seconds.
The first task selection had nine failures from **one** implementation defect:
the PNG renderer emitted RGB while the inherited closed PNG contract requires
RGBA. The renderer now explicitly produces RGBA; validation was not relaxed.
Two initial native tests also assumed a host `/usr/bin/ffmpeg`, which is absent.
Their test-only injected runner now uses the explicitly provisioned private
FFmpeg/FFprobe prefix; production container paths remain fixed and no host
packages were installed.

The immutable-image, two-container HTTP chain is the next acceptance step.
These local component observations use explicit synthetic authority and are
not production release evidence. Shared management authority was extracted
to avoid duplicating scope checks (SRP/DIP); the existing signed server and
bounded subprocess runner are reused through their transport/execution seams.
