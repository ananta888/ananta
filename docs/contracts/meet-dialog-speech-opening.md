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

## Implementation and technical observations

The assigned dialog runtime now begins or polls one owned opening per tick,
then transfers its validated receipt exactly once to `SpeechPublication`.
Text correlation is reserved before source startup; a later audio setup failure
does not retract delivered text, replay the answer or select a fallback voice.
PCM shape and the existing publication bounds are checked before deferred IO.
Timeout, stale authority, voice ABA, navigation and malformed receipts clear
pending PCM and cancel only the opening's generation. The ten-second setup,
2.5-second control freshness and 200 ms PCM queue limits are unchanged.

Opening state and its narrow browser port are separate from PCM accounting
(SRP/ISP/DIP). The synchronous browser API remains available for existing
standalone consumers and explicitly injected legacy ports (LSP); production
dialog composition selects the nonblocking lifecycle. Existing dialog output
still coordinates binding, acceptance and publication; this preserved SRP
limitation is not expanded into another scheduling loop or a broad media facade.

The real private-browser delay gate ran against companion revision
`56cdeb335d015fed2e60b06ce594d72da1a37581`. Its first attempt failed in
28.67 s at initial moving-screen reception, before speech setup: packets arrived
but no remote video frames decoded. The cause is not established. A single
repeat passed in 48.87 s. Native speech-worklet loading was delayed by three
seconds without changing authority or source watchdogs; both openings completed
in 3126.54/3101.41 ms. The first 220500-sample output completed locally and
arrived as correlated non-silent remote audio. The second selected voice was
interrupted; local/remote revocation took 633.15/637.46 ms, while screen and chat
remained enabled. No physical capture or transform errors were observed.

This proves the bounded delayed-start scenario, not a clean combined browser
batch, arbitrary host-load resilience, real GPU speech or public TURN delivery.
Audio/profile admission was explicitly synthetic and no production evidence
identity was issued. Existing unrelated Compose/data changes and the running
companion deployment were left untouched.

The final container-free regression passed all 187 opening, browser-port,
publication, speech-output, voice-projection, chat-race, control-exchange and
observer tests in 71.45 s with two Pytest workers. It includes rejection of
empty, oversized, odd-length or non-byte PCM before browser IO in both start
modes and execution of the exact delay/cancellation JavaScript. Targeted Ruff
lint/format checks, diff whitespace and canonical TODO consistency passed.
