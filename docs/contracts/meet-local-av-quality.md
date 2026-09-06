# Fixed-profile decoded A/V quality gate

Every local avatar/persona MP4 is now decoded and checked before it can leave
`encode_frames` or reach the existing publication path. This is an offline
codec/timebase gate, **not** a live Meet receiver, TURN, lip-sync quality or
network-jitter acceptance claim.

The existing bounded subprocess runner invokes `/usr/bin/ffprobe` with only
`pipe` protocol access and MP4 bytes on stdin. There is no caller-selected
decoder executable, URL, external media reference or filename. Limits are
3.5 MB input, 2 MiB JSON output, 5 seconds decoder execution and 2048 decoded
frame records. The runner supervises the process group and polls the existing
current-authority checkpoint; cancellation cannot return a successful report.
The enclosing worker turn still has its original hard deadline and container
resource limits.

`ananta.local-avatar-av.v1` has these explicit bounds:

- Exactly one H.264 video stream, 256×256, 12 FPS, and one AAC mono stream at
  22,050 Hz. No alternate tracks or silent format conversion count as success.
- The expected uncompressed speech sample count is 1 through 882,000
  (40 seconds). Decoded video count must equal its 12-FPS ceiling.
- Every decoded audio/video timestamp follows the sample/frame clock from
  zero with at most 2 microseconds decimal-report tolerance. Missing, repeated
  or non-monotone frames fail rather than being hidden by average frame rate.
- Decoded AAC padding must be nonnegative and less than 1024 samples; the
  absolute end-time difference of the two decoded streams must be ≤150 ms.
  Padding and frame quantization are reported separately from input duration.

The semantic validator retains counters only. Decoder JSON and raw media remain
temporary runtime data, not ordinary audit artifacts. The report's
`live_delivery_verified` is always false. The generated procedural face is
still visibly synthetic amplitude animation, not a generative talking head.

Source generation, encoding, bounded process execution and decoded-clock
validation remain separate responsibilities (SRP/DIP). The fixed profile
deliberately rejects alternative codec/rate adapters; those require their own
declared profiles, not an implicit weakening of these checks.

Headless negatives are in `tests/test_meet_av_quality.py`. The opt-in
`MEET_AV_QUALITY_GPU_GATE=1` test generates actual local Piper CUDA speech and
NVENC video, then decodes both tracks. An initial private reference probe
decoded 28 video frames, 50 audio frames and 51,200 AAC samples from 50,432
speech samples: 768 padding samples and 11,338 µs end-time difference, with
1.95 seconds total probe time. It used no microphone, camera or human input
and is explicitly synthetic technical observation, not production evidence.

The combined private hardware/browser and decoder-regression batch passed all
48 checks in 60.93 seconds with worker image
`sha256:b1d22619d9a82fe8c3214539ce810991f0dad6ef1ae0dfd62e576d8365eff0de`.
No public Meet or Hub service was restarted for this test.
