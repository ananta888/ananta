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

## Implemented and verified

`BrowserSpeechPlayback` validates the source receipt and monotonic progress;
`BrowserPcmFeeder` owns the narrow browser transport, with fixed scripts in a
separate module. Production dialog composition selects this delivery strategy
after deferred opening and clears its Python PCM reference after transfer.
Explicit synchronous opening and injected legacy producers remain compatible.
Fresh `DialogSpeechOutput.update` calls alone pulse the controller. Each pulse
carries the remaining absolute Hub window, rather than granting another full
2.5 seconds after an arbitrarily delayed RPC. The browser also bounds monotonic
age and checks local lease/chat/source/URL before filling the unchanged queue.

The initial 107 focused regressions passed in 46.63 s; the expanded publication,
opening, voice, observer and feeder regression passed 168 tests in 65.63 s.
Exact-script tests run four seconds of consumption with no Python status poll
or PCM RPC, and exercise controller loss, rollback, replacement, partial batch
failure, invalid progress and zeroing retained storage. Later targeted helper
checks cover the deterministic stall instrumentation separately: all 42 feeder,
playback, delay and stall checks passed in 25.44 s.

Serial private browser checks with the current isolated Meet build:

- **50.25 s, passed:** 28 deliberately delayed Python status polls of
  354.82–366.41 ms; first local output completed all 220500 samples with
  correlated non-silent remote audio. The second selected voice was revoked;
  local/remote removal took 872.20/883.59 ms. Screen remained usable.
- **222.66 s, passed:** generations 1–4, three actual lease renewals and matching
  image hydration, decoded blue avatar and moving screen after each, five full
  local replies of 220500 samples each. Image removal took 1099.08 ms locally
  and 1134.05 ms remotely; another reply then completed before parent stop.
- **54.28 s, two passed:** a fresh-Hub-armed 4500-ms Worker polling stall still
  delivered new remote audio after 500 ms, then the browser removed audio at
  2494.75 ms, **before the Worker returned**. The stale runtime subsequently
  terminated and its actual Hub task became failed; no completed reply/replay.
  Separate Hub parent cancellation stopped local/remote speech in 514/522 ms.

All media/policy fixtures in these checks are explicitly synthetic. No real GPU
or public TURN acceptance, remote sample-accounting guarantee or production
SRC/RUN evidence is claimed. The previous old-build and PCM-underrun failures
remain in [the renewal ledger](meet-image-renewal-series.md).
