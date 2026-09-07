# MAP-23 stored-clip acceptance scope

This audit maps the existing four MAP-23 criteria to the implemented bounded
clip path. It is not acceptance of the complete autonomous Meet track.

| Existing criterion | Implementation and checks |
| --- | --- |
| Versioned assets and separate optional renderer | Immutable MP4/PNG catalog and storage, separate video inspection/task/receipt adapters, `PersonaClipFrames`, and `MeetVisualSelections`; direct clip and profile-bound turns coexist with static images/procedural avatars. Storage/CAS, scoped profile, renderer selection and lifecycle tests cover these boundaries. |
| Explicit imported/generated labels and usage consent | Opaque AI/imported/generated/test frame labels; video-specific policy with separate source/license/consent pins, explicit usage purposes, current Registry receipts and revocation checks. Closed policy tests reject missing required consent and implicit source/usage expansion. No talking-head model or likeness permission is inferred. |
| Explicit format/resources and capability failures | Fixed H.264/NVENC, 256×256/12 FPS, forty-second turns, at most 120 retained decoded source frames; four encoder surfaces with no lookahead/B-frame expansion; 2-GiB speech CUDA arena with verified effective options. Closed signed capability errors reach the Hub without private diagnostics or CPU/cloud fallback. The explicit static-image-plus-speech mode remains separately selectable and honestly labelled. |
| Actual moving clip and audio decode | Actual current-source RTX/Piper/NVENC probes render an imported synthetic moving motif with generated speech, then decode video and audio and check sample counts, end skew and distinguishable frames. These runs prove this local renderer path, not generative-video quality. |

The memory bounds above cover the controlled frame buffers, encoder settings
and speech-provider arena. They do **not** promise total GPU/driver VRAM
isolation or arbitration of unrelated host workloads. Those wider resource
and concurrent live-source questions belong to MAP-24. Model/driver overhead
must not be misreported as part of the arena quota.

Cross-references: [clip inspection](persona-video-inspection.md),
[video policy](persona-video-policy.md), [bounded turn wiring](meet-persona-clips.md),
[profile selection](persona-profile-videos.md), [clip picker](persona-video-discovery.md),
[speech limits](meet-speech-profile.md), and [safe errors](meet-media-failures.md).

Still separate/open: dynamic voice assets and independent live TTS publishing
(MAP-22), common live clocks/interruptions and shared-resource admission
(MAP-24), continuous session source switching (MAP-20/21), multi-agent operation
(MAP-28), public/forced-TURN validation (MAP-31), and production/rollout evidence
(MAP-30/32). Completing this bounded asset/renderer task does not complete those
dependencies' broader criteria or authorize archiving the overall TODO.

No `SRC_*` or `RUN_*` identity is invented for these local observations. The
policy/inspection integration tests use Registry-issued, explicitly classified
test identities and structural decoder doubles. The hardware probes are
synthetic local technical observations, not grounded production release claims.

Final criterion-focused rerun on 2026-09-07: 72 video-policy, video-profile,
turn-selection and labelled-decoder tests passed in 55.58 seconds. The separate
failure/HTTP checks and two current-source GPU probes are recorded in
`meet-media-failures.md`; no full-repository test run is implied by this audit.
