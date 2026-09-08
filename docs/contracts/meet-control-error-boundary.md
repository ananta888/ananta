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
