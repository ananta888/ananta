# Hub-controlled bounded dialog reconnect

MAP-11 source audit, 2026-09-09, Ananta `44016f299`, Meet `b11d749`.
Control reads already reconnect their HTTP transport with at most two retries,
100/200-ms backoff and the original 2.5-second freshness window. Three actual
lease renewals, Worker loss/stall and real Hub process restart are verified.
However, `require_dialog_session` intentionally terminates on membership loss;
`DialogSessionOperations` never retries an old grant. The persistent phase
record also rejects replacement membership. Room rejoin is not implemented.

## Required implementation boundaries

1. Add an authenticated Meet retirement receipt for one exact v2 session and
   immutable Hub binding. It only removes that session; it cannot admit a peer,
   extend a lease, select another room or issue receive consent. Unknown/already
   absent session IDs produce an idempotent absence receipt under the verified
   caller binding, never evidence of prior ownership. The Hub must already
   possess its separately validated original membership before using that
   receipt. A present foreign binding must fail without touching that session.
2. Keep recovery admission in the Hub with durable assignment-scoped state:
   remember current validated membership, consume a bounded recovery attempt
   atomically, retire the old membership and wait for cleanup before issuing
   one fresh grant. The original Task/dispatch/runtime/source rights and total
   deadline remain unchanged. No post-hoc replacement of the assignment or
   automatic replay of old audio/chat/transcripts.
3. Negotiate recovery explicitly and bind it to preauthorization. Default
   legacy sessions retain fail-closed behavior. Hub loss, uncertain retirement,
   revoked policy or exhausted attempts produce a bounded failure, not a new
   independent Worker retry loop. The Worker may execute only the exact
   Hub-authorized reconnect and refresh its resource watchdog from fresh Hub
   replies; media remains closed while reconnecting.
4. Keep ordinary phase transitions strict. Only the Hub's separately validated
   recovery transition may replace old membership and invalidate its source
   observations. After joining, validate the new actual Meet membership epoch
   and current source controls before recreating source pumps. Late grants,
   callbacks and completions cannot restore the old generation.
5. Verify scope substitution and idempotent retirement, durable recovery races
   and restart, unchanged deadline/watchdog bounds, and real packaged Worker
   connection loss/rejoin with a separate receiver. No task completion is
   claimed from an isolated primitive or a successful retry alone.

SRP/DIP: persistence, recovery policy, authenticated Meet transport and owned
browser lifecycle remain separate narrow ports. The broad existing dialog
composition and Meet HTTP dispatcher are preserved SRP debt; do not add a
second scheduler to either. Retirement receipts are control-plane facts, not
SRC/RUN release evidence. Initial tests use explicit synthetic policy only.

## Retirement transport implemented

Meet now confirms exact v2 session retirement only after synchronous removal
of its owned registry member. A failed detach retains task occupancy; unknown
IDs do not claim prior ownership. Ananta validates the closed receipt, exact
nonce/session/immutable binding, fixed TLS origin/path, bounded body and current
Hub authority on both sides of the call. It never retries an uncertain request
or treats the receipt as a new lease. `membership_binding` is the shared pure
identity projection, avoiding divergent validation between live membership and
retirement (SRP). Existing authorization/observation consumers stay compatible.

118 targeted Ananta checks passed in 49.03 seconds; 61 companion checks passed
in 0.832 seconds, including real signed HTTP/WebSocket detachment, replacement
admission and late old-ID replay. Logs `/tmp/ananta-meet-retirement-hub.log`
and `/tmp/ananta-meet-retirement-final.log`. These establish the transport
primitive, not automatic reconnect or MAP-11 completion. Durable recovery
admission, negotiated Worker execution and actual recovery acceptance remain.
