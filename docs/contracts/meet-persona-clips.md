# Stored persona clips in bounded Meet turns

This additive path uses admitted private video assets, not a human camera and
not a talking-head model. It is separate from the still-open continuous dialog
avatar and live-delivery acceptance work.

An authorized media caller selects a clip explicitly:

```json
{
  "text": "A bounded local reply",
  "persona_video_id": "admitted-video-artifact",
  "video_repeat_mode": "loop",
  "publish_to_meet": false
}
```

`hold_last` is the other supported repeat mode. Omitting the mode, combining a
clip with an image/profile selector, or supplying unknown fields is rejected
before task creation. Neither an asset ID nor a preview permission grants room
publication. Publishing still requires the separate project policy, current
video publication permission and the existing machine issuer/room binding.

The Hub's `MeetPersonaVideos` adapter verifies exact tenant/project ownership,
video-only policy, current private catalog revision and registered inspection
receipt before and after reading both the normalized MP4 and PNG. Revocation
of either permission or receipt prevents releasing the assignment. A clip
preview uses the full normalized clip under preview authority; it does not
become a public download endpoint.

The closed Worker assignment carries an immutable video reference, the bounded
normalized bundle, admitted origin kind and explicit repeat mode. Profile
ancestry, registry credentials, filesystem paths and publication authority are
not inferred from these bytes. Unknown or mismatched kinds/digests/scopes fail.
Generated sources cannot be labelled production. TaskQueue metadata retains
only the selected reference, repeat mode and purpose, never the clip bytes.

The Worker uses the same Hub lease and pinned local speech pipeline as the
static-image path. A separate visual-renderer adapter chooses the static image
or stored clip; its decoder budget is at most twenty seconds and never exceeds
the parent deadline. The existing bounded 256×256/12-FPS frame renderer labels
AI/imported/generated/test content and encodes via NVENC. The existing publisher
consumes that MP4 unchanged. The result declares `persona-clip-h264_nvenc` and
the exact video reference; the Hub checks the response and current permissions
again before completing or disclosing it.

Structure: private asset policy/storage, closed wire validation and rendering
remain separate (SRP/DIP/ISP). `MeetTurnService` now delegates the previously
repeated image/clip/profile selection and authority branches to focused
`MeetVisualSelections` strategies. The temporary bound selection carries the
exact revalidation port but is never serialized to a Worker. The Hub task
adapter still lives alongside the turn service, a remaining modularity debt;
no task orchestration or publication policy moved into a visual adapter.
The extraction passed 110 image, clip, profile, turn and capacity regression
tests in 79.62 seconds without changing the existing request fields.

## Verification and limits

The new tests use actual private SQL asset/policy/Registry fixtures with an
explicit structural inspection double. They cover selection, read-time
revocation, actual Hub task/lease metadata, pre-dispatch revocation, returned
reference/engine mismatches and compatibility with image turns. A test that
expected only two project-access checks was updated to include the new check
immediately before dispatch, also when no capacity adapter is injected.
The combined clip/image/profile/speech/capacity regression run passed 186 tests
in 129.19 seconds. Follow-up bootstrap and runtime-composition checks passed
another six tests in 10.65 seconds and 18 tests in 19.59 seconds respectively.

The actual GPU probe `worker.meet_media.persona_visual_smoke` exercised the new
closed assignment and renderer using local Piper/CUDA, a moving imported test
motif and NVENC. It ran in a separate read-only UID-1000 container with no
network, no service credentials, bounded resources and only read-only model,
source and driver-library mounts. The first attempt exposed the parent/decoder
deadline mismatch, now covered by a regression test. A second failed because
the isolated fixture omitted needed driver libraries; supplying the same exact
read-only library bindings as the running worker resolved it without changing
the host or service.

The corrected probe passed in 2.50 seconds: 42 decoded video frames, 76,544
speech samples, 256 padding samples and 17,007 microseconds end skew; the first
two decoded video frames differ. This is a synthetic local technical
observation, not Registry-backed production evidence, generative video quality,
LLM inference, or decoded live Meet delivery. No running service was redeployed.
The following sections document subsequent profile and UI wiring; live-delivery
and production acceptance remain separate.

## Encoder working-set follow-up

The shared image/clip/procedural renderer now explicitly selects four NVENC
surfaces, zero lookahead, no B-frames, zero async delay, ultra-low-latency tuning
and disabled multipass. These settings avoid implicit surface growth from
lookahead/reordering in the deployed
[FFmpeg 5.1 encoder](https://github.com/FFmpeg/FFmpeg/blob/release/5.1/libavcodec/nvenc.c).
They bound the selected encoder buffers, **not** total VRAM: driver contexts,
codec-internal allocations, speech models and other host processes remain
outside that bound. Existing 256×256/12-FPS and forty-second turn limits remain.
Missing/failed NVENC and encoding timeouts now produce distinct content-free
internal errors, never an implicit CPU codec fallback; the outer Worker still
returns its stable generic execution-failed contract.

The focused renderer/decoder/quality suite passed 61 tests in 44.78 seconds.
The isolated RTX probe with the new options passed in 2.71 seconds, decoding
43 video frames and 77 audio frames, with 512 audio padding samples and 7,460
microseconds end skew. The moving motif remained distinguishable. This remains
a synthetic technical check, not a VRAM quota, generative-quality or live-
publication acceptance claim. No running service was changed.

The hardware checks are now repeatable against current source without a
service rebuild:

```sh
MEET_CURRENT_SOURCE_GPU_GATE=1 .venv/bin/python -m pytest \
  tests/test_meet_gpu_source_fixture.py tests/test_meet_current_source_gpu.py \
  -q -n 0 -o cache_dir=/tmp/ananta-meet-current-gpu-pytest-cache
```

This explicit local gate requires the provisioned media-worker image, local
model files and the existing read-only host-driver bindings. It starts a
separate random-named container from that immutable local image, mounting only
the current media/contract source packages, models and allowlisted driver
libraries read-only. It copies no service credentials, environment or state,
has no network or public ports, drops capabilities, runs as UID 1000, caps
host memory/PIDs/temp storage, and enforces a 45-second inner deadline plus
bounded host commands. Exact owned-container cleanup runs on success, timeout,
bad reports and uncertain creation. No image is pulled and no service is
restarted. The original serving-worker opt-in gates remain separate.

The fixture isolation suite plus both actual current-source clip/speech GPU
gates passed all 15 checks in 23.91 seconds on 2026-09-07. Their explicit opt-in
skips on ordinary test runs are not hardware acceptance claims.

## Profile-bound clip execution

The additive `persona_video_profile` selector now accepts the same exact
`organization_id`, `owner_kind`, `owner_id`, `selection_digest` binding returned
by the profile-effective endpoint. It requires explicit `video_repeat_mode`
and cannot be combined with `persona_video_id`, `persona_image_id` or the
legacy `persona_profile` image selector. The legacy selector retains its image
semantics; it never silently chooses a configured video instead.

`MeetPersonaVideoProfiles` uses the profile service's closed voice/video
execution policy. Disabled voice or video, unavailable clips, stale profile
digests, inactive owners and revoked project/organization access fail before
asset loading or dispatch. This adapter works with video-only installations;
image availability is not fabricated. Its new visual strategy keeps the
binding under `persona_video_profile` in the Hub task and never sends profile
ancestry to the Worker. Capacity checks and live Worker leases revalidate the
exact binding, including revocation while a turn is running.

The combined video/image/profile/turn/bootstrap regressions passed 142 tests
in 103.69 seconds, including actual Hub TaskQueue persistence and immediate
live-lease denial after profile replacement. This does not prove live Meet
delivery; Angular selection is tracked separately below as it is implemented.

## Angular profile selection

The organization/team/agent profile panel now independently edits stored clip
selection: missing, inherit, disabled or an admitted asset ID. Video references
have their own typed kind; the client rejects unknown fields, other kinds,
wrong project/artifact bindings and invalid revisions/hashes before preview.
The video-specific authenticated endpoint returns only a private PNG capped at
350,000 bytes. It does not fetch a public clip URL, autoplay media, capture a
device, join Meet or confer publication authority.

The local `PersonaVisualDraft` model shares image/video draft state without
owning HTTP or policy. The existing facade retains cancellation and scope
ownership, discards late callbacks and revokes both preview object URLs on
selection/scope changes or destruction. Saving preserves independent image
and clip selections; unchecked new IDs cannot be saved as admitted assets.
The video picker reuses the shared form-field primitive and remains a local
persona feature component, not a generic global media component.

All 31 targeted panel/API tests passed in 1.26 seconds; feature ESLint and
Angular template/type compilation passed. Compilation reports the pre-existing
unused `RouterLink` warning in the unrelated knowledge-hygiene page. The UI
currently selects clips by admitted ID, not by a new public asset listing.
Configuring a profile still does not switch a running dialog or generate a turn.

## Explicit clip turns in the Meet assistant

The Meet assistant now offers a stored-clip source beside the existing simple
avatar. Choosing the source does nothing by itself. The user must specify an
admitted clip ID, explicitly choose loop/hold-last and start a response. The
publication checkbox remains independent and off by default. The closed UI
choice maps to the same Hub API fields; paths, URLs, unknown source kinds,
extra grant fields and implicit repeat modes are rejected before HTTP.

Account/project changes and cleanup clear the clip and repeat selection. The
existing request cancellation and bounded media preview lifecycle remain in
place. This still is not a live-session source-switch control or an automatic
subscription to saved profile changes.

The combined Meet/persona frontend suite passed 79 tests in 1.58 seconds. Its
form tests use actual automated Angular change events, including explicit mode
selection, rather than unnotified plain-field mutations. Feature lint and
Angular type/template checks passed; the production bundle built in 22.405
seconds. Existing unrelated RouterLink/CommonJS optimization warnings remain.
