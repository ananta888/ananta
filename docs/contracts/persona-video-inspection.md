# Bounded local persona clip inspection

MAP-23 now has a separate CPU inspection adapter and closed result contract.
This is a technical foundation, not a video upload API or publication grant.
Existing image policies, receipts and stored assets remain unchanged.

Input is MP4/H.264, at most 3,500,000 bytes, 1280×720, 30 fps,
300 decoded frames and 10 seconds. At most one optional mono/stereo AAC
stream at 16/22.05/44.1/48 kHz is accepted. Other streams/codecs, inaccessible
pipe-only containers and decoder failures are rejected. Source audio is
deliberately removed; importing a clip does not authorize voice replay.

The normalized profile `ananta.persona-clip.h264-256-12.v1` is silent H.264,
256×256, 12 fps, 2–120 decoded frames, at most 1,500,000 bytes. A separate
RGBA PNG preview is limited to 350,000 bytes. Source, video and preview
digests are checked through a closed size-bounded Base64 result contract.
These content hashes are neither rights nor Registry evidence identifiers.

Process execution, pure profile validation and normalization have separate
responsibilities (SRP). The subprocess runner is injected (DIP). Every step
uses pipe-only input, bounded output, a fixed overall deadline of at most
30 seconds, an individual 3/8/3/3-second budget and repeated caller authority
checks, including subprocess cancellation. Corrupt-frame errors are fatal.
The worker container must provide aggregate CPU/memory limits; FFmpeg's
allocation option is a per-allocation cap, not a whole-process memory limit.

`tests/test_persona_video_inspection.py` covers closed results, codec/time/
dimension bounds, all nine authority checkpoints, invalid deadlines and
sanitized failures. No approval or human capture is used.

`PERSONA_VIDEO_CONTAINER_GATE=1` enables
`tests/test_persona_video_container.py` against the provisioned private
worker. Its built-in moving test pattern plus synthetic sine audio is
normalized and decoded again. The first/last RGB frames must differ,
the normalized probe must contain no audio, and actual decoded frame count
must match the result. The initial local run took 0.43 seconds, producing
13 frames (1,083 ms), 87,671 video bytes and 21,982 preview bytes.
This is a synthetic local technical observation, not production-release,
live-delivery, lip-sync or generative-video quality evidence.

The final combined local run passed 74 tests in 52.73 seconds, including
this actual clip decoder and the unchanged static-image NVENC renderer.

Still required: explicitly video-scoped rights, Hub-issued inspection
assignments/receipts, immutable video catalog/storage/lifecycle, profile
selection and lease-bound publication integration. An image-only policy
must not silently grant any of those video operations.
