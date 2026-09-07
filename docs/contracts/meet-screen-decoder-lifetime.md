# Screen decoder lifetime across source generations (MAP-14/15)

Source audit 2026-09-08: Meet's `MachineScreenSource.close()` resets its `busy`
activation flag even when `ports.decode()` has not settled. An immediate reopen
can start a second native decode; a timed-out native promise likewise remains
alive after its one-second observation timeout. Repeating generations can grow
native work despite the advertised one-frame bound. Existing tests wait for the
old decoder before sending the replacement and therefore miss this case.

Planned correction: track actual decoder occupancy separately from publication
generation. Stop still immediately invalidates the source, closes its track and
wipes input buffers. A new activation must fail with a fixed bounded
`meet_screen_decoder_busy` while that exact old native decode is outstanding;
neither close nor timeout releases native occupancy. Actual settlement releases
it, disposing late bitmaps without drawing them. No decoder queue, timeout growth,
new permission or speculative crypto change. Ordinary start/frame/stop and
existing source identities remain unchanged.

First reproduce close/reopen and timeout/reopen overlap with a pending decoder
port. Cover late resolve/reject, synchronous failure, no stale draw and successful
fresh activation after settlement. Then verify real screen/other-source/renewal
behavior with the existing private Chromium/Firefox matrix and a current private
build. A hung native decoder produces a bounded failed source, not indefinite
waiting for a human or unbounded replacement work. Scope is the installed
offline agent-browser source, not general web-page privacy or public TURN.

The correction keeps native resource lifetime separate from source authority
(SRP), reuses the narrow decode port (DIP/ISP), and preserves the Hub as sole
owner of tasks. The original intermittent encrypted first-frame failure remains
a separate unresolved observation; this resource bug is independently testable.

## Implemented and private integration verified

Meet commit `f6d1d90` separates native occupancy from activation, retains the
budget after timeout/close, wipes the owned input bytes on stop and disposes late
bitmaps. It does not pretend that native browser decoding can be cancelled or
that every browser/GPU memory copy can be erased. The deterministic pre-fix
regression failed (one failure, eight passes); twelve source and thirteen avatar
unit tests now pass, as does Angular application typechecking.

A fresh private production build passed four serial Chromium/Firefox cases in
25.89 s: eight denied reopens while one bitmap is outstanding, one late bitmap
disposal, then an actually decoded fresh green screen. Simultaneous moving
avatar, audible speech and red/green/red screen also survived three lease
renewals per browser with three stable transceiver slots and zero capture or
transform errors.

The real private Hub/Worker `screen-decode-latency` composition then passed in
44.32 s. Forty-seven frames were deliberately delayed (307.69–442.26 ms observed),
while one 220,500-sample speech output completed locally and correlated non-silent
audio arrived remotely. Subsequent voice revocation stopped local/remote output
in 551.66/565.78 ms while the screen remained usable. Audio/model/profile inputs
were explicitly synthetic, not a GPU or production release test. The complete
isolated Meet `npm run check` for `f6d1d90` also exited zero: 639 frontend tests,
560 Node passes / three visible Node skips / zero failures, Node 140.35 s.
Build/Go/TODO/security checks passed; external infrastructure gates remained
explicit skips. Serving assets, services, public trust and user work were not
changed.

## MAP-14 acceptance scope

- Explicit adapter: `OwnedDialogScreen` owns a fresh offline 640×360 browser
  context; `DialogScreenPump` follows Hub source control; `BrowserScreenFrames`
  supplies only the current generation to Meet's separate synthetic source port.
  No human display picker, host profile or capture API is used.
- Bounds: latest CDP frame only, 350,000-character transfer / 262,144 decoded-byte
  JPEG limit, 5 FPS, one outstanding native decoder, fixed 1 s decode / 1.5 s
  delivery / 2 s stall budgets, and existing adaptive sender bandwidth and
  negotiated codec/SFrame policy. Closing a generation cannot reset occupancy.
- Actual remote moving content and independent sources are covered by the
  private Hub composition and Chromium/Firefox renewal matrix above, not a
  screenshot file or merely increasing sender counters.
- Start, renewal and stop are fully headless; capture counters remain zero.
  Unknown browser pages, Browser-Use/Camofox navigation admission, comprehensive
  privacy filtering and public deployment remain MAP-13/15/30/31 work. This
  adapter acceptance does not close those tasks or the entire media TODO.
