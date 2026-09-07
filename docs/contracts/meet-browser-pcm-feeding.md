# Browser-local feeding of an already authorized spoken reply

## Source check and boundary

`DialogSpeechOutput` holds one Hub-authenticated, already generated PCM result
(at most 40 seconds). It currently fills Meet's 200-ms worklet queue through
Python/browser RPCs. A real 287.9-ms speech RPC caused a verified worklet underrun
even with timely Hub callbacks. Nonblocking source opening and screen decoding
do not eliminate that cross-process dependency.

Move only delivery of this one bounded result into a narrow browser-local
playback adapter. It must neither generate content nor schedule tasks, pick a
voice, open another session or fetch anything. Keep the existing Meet source
API, 20-ms chunks, 200-ms worklet queue, 40-second content cap and 50-second
source cap unchanged. The browser may hold the same bounded delegated PCM asset
previously held by the Python producer; this is not an enlarged worklet queue.
Retain existing synchronous/injected producer ports for compatibility.

## Required checks before acceptance

- Start only after chat correlation reservation, validated source receipt and a
  fresh Hub checkpoint. Exact local lease, chat admission, source generation,
  URL and absolute deadline remain mandatory before every batch.
- Only fresh authenticated Hub updates may pulse the browser controller.
  Python ticks/polls do not renew it. Expired pulses cannot revive playback.
  Bound both wall-clock validity and monotonic controller age to at most 2.5 s;
  delayed delivery, navigation, lease change, rollback and old tokens fail closed.
- One owned feeder, at most ten 441-sample frames per batch, no duplicate writes,
  no autonomous reopen/retry. Late callbacks cannot affect a replacement.
- Stop, invalidation, failure and completion cancel the timer, wipe retained
  byte storage and close only the owned generation. Progress observations
  contain counts/status only; never publish PCM or credentials in diagnostics.
- Test exact browser scripts with deterministic clocks/ports, including delayed
  Python polling beyond 200 ms, stale controller, current revocation, malformed
  progress, navigation, generation races and partial batches. Then run the real
  three-renewal and live-interruption gates serially, retaining failed attempts.

SRP/ISP/DIP: the new adapter owns PCM delivery only; Hub control, speech binding,
source setup, source authorization and synthesis retain their existing owners.
The existing runtime/test composition size is preserved architectural debt,
not a reason to embed another scheduler or policy engine in either browser.
This does not promise glitch-free audio under arbitrary browser/main-thread
starvation, GPU availability, remote sample accounting or production evidence.
