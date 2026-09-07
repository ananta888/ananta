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
