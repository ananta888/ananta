# Nonblocking dialog speech-source opening

## Source check and scope (MAP-12/22/24)

The Hub control read is now single-flight and nonblocking, but
`DialogSpeechOutput.accept` still constructs `SpeechPublication` synchronously.
`BrowserSpeechPort.open` polls a browser Promise in an inner loop for up to ten
seconds. Its checkpoints enforce the cached 2.5-second Hub projection; the
outer assigned runtime cannot consume fresh Hub reads during that loop. A valid
slow source start can therefore fail despite ongoing Hub authorization. This
is a source-confirmed limitation, not a proven attribution of the earlier
intermittent missing second chat reply.

## Incremental design

- Add a small opening lifecycle, polled only by the assigned runtime. Each tick
  performs at most one start/status browser RPC; no inner wait or new thread.
- Retain exact owned browser operation token, original ten-second setup deadline,
  current Hub/lease/chat/voice checks and generation-conditional cancellation.
- Pending opening counts as busy, so no new input can enqueue another source.
  Hold only the existing bounded PCM result. No samples are sent until browser
  source receipt validation and successful text-correlation reservation.
- A new ready receipt is transferred once to the existing publication lifecycle;
  malformed, expired, replaced, revoked or failed starts cancel only their owned
  generation. Never replay or silently fall back to another source.
- Preserve synchronous `BrowserSpeechPort.open` for its existing standalone
  consumers. Keep opening state separate from PCM progress/queue accounting
  (SRP/ISP), with injectable narrow browser/clock checkpoints (DIP).

## Verification

Deterministic clocks and pending browser phases must prove that several seconds
of setup can span fresh Hub updates without blocking media ticks. Revocation,
navigation, setup timeout, malformed receipt, voice ABA and late completion must
clear pending PCM and prevent publication/replay. Exercise the exact JavaScript
token/generation boundary and preserve existing publication/browser-port tests.
Then inject bounded source-start delay into the real private-browser gate and
require correlated text, complete local PCM, remote audio and bounded stop.
No human approval, physical capture, production deployment or fabricated
SRC/RUN identity is involved; public/GPU release acceptance stays separate.
