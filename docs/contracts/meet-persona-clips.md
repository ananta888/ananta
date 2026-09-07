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
Video-profile execution selection and Angular video selection remain next.
