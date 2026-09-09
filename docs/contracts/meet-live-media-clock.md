# Bounded live media clock: MAP-24 implementation plan

Source audit: Ananta `cfe01b6cc`, Meet `12235df`, 2026-09-09. Speech already
has exact PCM sample progress; avatar video uses an independently owned muted
decoder; avatar/screen canvas publications have separate generation and stop
lifetimes. The browser currently exposes no common live timing projection.
Offline MP4 end-time validation is not a live receiver synchronization proof.

Implement the following additive slice before the hardware/soak acceptance:

- One machine-page-owned monotonic timeline, bounded by that page's membership
  generation. Source lifecycle tokens are separate from identity, Hub grants
  and evidence. Never compare raw timestamps from different processes.
- A small source-neutral timing guard maps independently started media clocks
  to that timeline. Observe speech sample progress, decoded avatar-video media
  time, and screen/canvas frame submission separately. Record both observation
  time and media position, never disguise a submission timestamp as delivered
  RTP or decoded receiver evidence.
- An additive closed timing probe/projection identifies actual support and
  measurement kind. Existing source receipts and legacy capabilities retain
  their exact shapes. An explicitly negotiated Hub execution option requires
  the new port; an old browser cannot silently satisfy it.
- The initial fixed profile is independent persona video plus speech, not
  generated lip synchronization. Predeclare maximum source-clock drift of
  500 ms and observation age of 750 ms after source readiness. Existing source
  setup, 2.5-second Hub freshness, original Task/lease and recovery bounds are
  never extended by a timing observation. Invalid/regressed clocks, stale
  generations and exceeded thresholds fail closed and discard pending source
  output; a source restart cannot reuse its previous timeline lease.
- Preserve the existing Hub GPU FIFO admission and speaker floor. Audit and
  compose session and aggregate publication budgets at Hub admission, not in
  a Worker loop; fixed dimensions/rates/queues and exact publisher role remain
  upper bounds. No host-wide arbitration of unrelated GPU jobs is claimed.

The canvas API requests capture without accepting an application timestamp;
therefore this implementation must not claim it rewrites browser RTP clocks.
Web Audio also has its own rendering clock. These boundaries follow the
[W3C canvas capture specification](https://www.w3.org/TR/mediacapture-fromelement/)
and [Web Audio clock definitions](https://www.w3.org/TR/webaudio-1.1/).

Verify the pure guard with virtual clocks and hostile closed projections;
verify late callbacks, independent close, source replacement, loop/hold video,
audio underflow and clock regression in actual source adapters. Then exercise
real Chromium/Firefox receiver timing, synthetic synchronized markers, and
separately the installed GPU/TTS/video path under a declared hardware profile.
Report source drift, receiver measurements and semantic lip-sync capability
as different facts. Failed quality gates stay failed, never merely logged.

SRP/DIP: clock math, source observation, browser transport, Hub profile admission
and receiver-quality measurement are separate small ports. Preserve the
existing broad machine-page/Worker composition for now; do not add another
scheduler, global identity issuer or policy authority there. MAP-24 remains
partial until these implementations and actual acceptance are complete.
