# Preserve terminal Meet failures across Hub control callbacks

## Source audit

At `5363b80fa`, the new Worker recovery classifier distinguishes its own TLS,
signature and schema errors from transient transport failures. However,
`MeetAuthorizationClient` collapses unknown exceptions, including TLS and
upstream HTTP denials, into HTTP 503. Its existing validated-contract errors
can use HTTP 502. Both codes resemble a temporary gateway failure to a Worker.
No failed response grants fresh authority, but a known terminal error should
not acquire a retry through this second transport boundary.

Share the existing pure transient HTTP-read classifier in `ananta_contracts`;
keep the Worker-specific action and signal adapter separate. The Hub may emit
its established `meet_authorization_unavailable` 503 only for the closed
transient set. Other transport/parser errors become a content-free terminal
failure, preserving existing domain errors and closing unread HTTP-error bodies.

For authenticated dialog callbacks, add a restrictive terminal error header
to existing domain error responses, except the exact eligible temporary
unavailable error from an exchange. Preserve body/status schemas and successful
response signatures. Presence of this header can only prohibit recovery; it
never grants authority or permits retries. Missing headers retain compatibility
with old Hubs and ordinary gateway outages, inside the unchanged finite window.
Do not retry writes or parse remote error bodies as policy.

Use deterministic upstream TLS/HTTP/parser checks and actual loopback HTTP
callback tests for terminal 502/503 and genuine temporary unavailability.
Exercise old successful authorization and source-CAS paths unchanged. SRP/DIP:
the shared classifier has no Hub/Worker orchestration dependency; the Hub
client translates transport failures and the route preserves error semantics.
This correction does not establish room rejoin or production outage evidence.

## Implemented correction and verification

The shared `http_read_failure` module now owns transport classification. Hub
authorization closes HTTP-error sockets and exposes `meet_authorization_failed`
502 for non-transient failures, while retaining explicit domain errors. The
authenticated callback adds `X-Ananta-Dialog-Terminal` for every such domain
failure except the exact exchange-only unavailable 503. The Worker treats any
value of that restrictive header, including empty/unknown values, as terminal.
It reads no error body and retains old-Hub/gateway compatibility when absent.
No successful response validation, timeout, retry count or authority changed.

Before correction, the new boundary assertions reproduced **29 failures and
12 passes in 45.17 s** (multiple cases of the same error-translation problem,
not 29 independent product defects). After correction, **96 focused checks
passed in 86.96 s**, followed by **57 checks in 46.48 s**. The latter includes
nine actual two-hop loopback HTTP cases: Worker to authenticated Hub callback
to upstream Meet-shaped test endpoint. Upstream 401/403/409/429/500 and malformed
200 stay terminal; only 502/503/504 remain eligible temporary signals. No fake
membership or signature approval is used to make those failures pass. Existing
membership, observation and concurrent source-selection checks stay green.
Another **58 legacy client, scheduler, signed transport, diagnostic and crash
fixture checks passed in 34.16 s** with two pytest workers. The preceding
selection attempt collected zero tests because one filename was wrong; it is
not counted as verification. The corrected selection above executed all 58.

SOLID review: the classifier is pure and shared without depending on either
runtime; callback error metadata is separate from retry scheduling. Existing
Hub imports of the historical Worker encode/body helpers remain coupling debt
(DIP), not broadened here. Moving those existing ports is a separate migration;
this correction introduces no duplicate HTTP reader or orchestration loop.

## Installed-image follow-up

Immutable Worker image
`sha256:547fc9e52c5a8ab28fdaae878aac9ded4241710490737984d0cb5010556d9855`
was built from `89fe9db32`. The first packaged selection stopped both cases
before resource creation because the existing private browser build had become
stale (**2 failed, 27.95 s**). Parallel Meet development had advanced to
`92f583f`. A separate clean worktree built that revision successfully and
passed **765 frontend tests in 11.08 s**; serving `dist` was not touched. This
is not a new complete companion `npm run check` claim.

The next selection passed the terminal-denial case but failed the recovery
fixture's final counts (**1 passed/1 failed, 80.58 s**): both screens could
become visible before the second read, and the fixture then cancelled the
first task before its required injected failure/recovery. Add an explicit
bounded recovery barrier and require moving remote screens again afterwards;
do not slow the product or loosen freshness. The final selection passed both
cases in **81.77 s**. Two independent Workers recover from their one temporary
503 each; an armed terminal contract 502 receives exactly one failed read and
zero retries, the first Worker ends and the survivor continues then obeys
independent revocation. Both cases retain actual signed terminal observations.
Fifteen helper tests passed in **16.66 s** before the barrier correction and
again in **16.48 s** after it.
All infrastructure and policy remain private/synthetic, without application
source mounts, GPU use or production outage claims.
