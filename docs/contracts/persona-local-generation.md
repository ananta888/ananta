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
