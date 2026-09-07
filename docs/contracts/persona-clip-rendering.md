# Local imported clip with separately generated speech

`PersonaClipFrames` is an optional frame-source adapter alongside the existing
static-image and procedural-avatar renderers. It does not open a room, mint a
lease, create tasks or treat normalized media hashes as publication rights.
The [Hub policy/profile/assignment integration](meet-persona-clips.md) now
binds this adapter to bounded turns. Feature configuration and current explicit
project/asset/publication authority are still required; local renderer tests
do not grant productive use or prove live Meet delivery.

The caller supplies a normalized clip, explicit origin/classification,
`loop` or `hold_last`, and a current-authority callback. There is no inferred
repeat mode. The actual stream is re-probed under the fixed clip profile and
decoded once to RGB, bounded to the pinned 2–120 frames (at most 23,592,960
bytes). Frames can only be requested for the existing maximum 40-second,
12-fps turn. Frame reads check authority before and after constructing an
image. Closing releases the retained byte buffer; this is not secure erasure.

Every frame receives an opaque label area with `ANANTA | AI` and either
`IMPORTED` or `GENERATED`, plus `TEST`/`SYNTH` when applicable. Source pixels
cannot obscure the label. Generated material cannot be classified production.
Origin/classification labels are descriptive, not proofs of likeness consent.

Normalization strips input audio. The renderer uses separate caller-supplied
speech and the existing NVENC encoder with its mandatory decoded A/V gate.
No fallback to human capture, a different codec or another voice is implicit.
The caller still owns the outer turn deadline/process-group supervision;
decoder deadlines alone are not a complete publication-stop guarantee.

SRP/DIP: decoding and labelled frame selection are separate from shared
encoding and its quality gate. The injected subprocess runner/current lease
callback permit deterministic resource and revocation tests. Existing
temporary-file/encoder lifecycle remains caller-owned and unchanged.

`tests/test_persona_clip_frames.py` passed 23 tests in 20.59 seconds. They
cover explicit repeat/hold, visible labels, scope-independent format checks,
invalid frame/duration budgets, revocation, mismatched decoded counts and
buffer release after both encoder success and failure.

`MEET_MEDIA_GPU_GATE=1` enables `tests/test_persona_clip_speech_gpu.py`.
The real private container generates a moving synthetic MP4 with sine audio,
imports it, discards source audio, generates pinned Piper/CUDA speech, renders
and NVENC-encodes a labelled loop, then decodes both final streams. The initial
run took 2.52 seconds: 42 video frames, 76,544 speech samples, 256 AAC padding
samples and 17,007 µs end skew. Two decoded final frames must differ.
The final combined run passed 98 tests in 70.80 seconds, including this
hardware gate, CPU clip import and the unchanged static-image GPU renderer.

All inputs are synthetic and fully headless. These results are local technical
observations, not Registry-grounded production evidence, live Meet delivery,
talking-head generation or lip-sync quality claims. No public service changed.
