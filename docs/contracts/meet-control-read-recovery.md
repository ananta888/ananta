# Bounded recovery of Hub control reads

## Source audit and scope

At `957d450cf`, `HubDialogClient.call` collapses every transport/protocol/policy
failure into one terminal error. `DialogControlExchange` is already a
single-flight asynchronous read with a 2.5-second freshness window, but even a
fast temporary connection failure ends the dialog. MAP-11 remains partial;
this change addresses only short control-read interruptions, not general room
rejoin, durable Hub recovery, Worker replacement or HA.

## Closed recovery boundary

Only the `exchange` operation may classify a small explicit set of transient
transport failures: HTTP 502/503/504, temporary DNS resolution, connection
refusal/reset, timeout and unreachable network/host. TLS/certificate failures,
redirects, unknown errors, malformed or incorrectly signed responses and all
policy/authorization denials remain terminal. HTTP 429 and 500 are not a
permission to retry. No URL, exception text, response body or key is retained
in the recovery signal. Other callback actions retain their existing behavior.

Use a separate pure retry-budget component, not retry logic in media sources.
At most two additional reads may be attempted, with 100-ms then 200-ms
backoff, inside the original failure burst's 2.5-second request budget and
any existing, unchanged control freshness deadline. An initial unavailable
read cannot publish anything before a validated state arrives. A failed read,
timer, retry or obsolete response never refreshes authority. Only a new
validated, current response resets the recovery budget. Refresh markers cannot
skip backoff or revive an exhausted window. Keep exactly one in-flight read;
no queued requests, Task dispatch, media replay or approval retry is added.

SRP/DIP: transport classification, retry-budget accounting and asynchronous
exchange scheduling are separate small modules. The existing client remains
the sole signature/schema verification boundary. Worker-local reconnection of
this fixed read port does not create Tasks or change Hub orchestration policy.

## Verification before closing this slice

Deterministic exception/clock/future tests cover exact classification, one
in-flight request, startup and established deadlines, exhausted attempts,
obsolete replies, close/revocation and unchanged signature rejection. Real
signed HTTP checks must exercise transient status then recovery and terminal
denials without retries of other actions. A private actual Hub/Worker/Meet
scenario must continue normal chat/screen behavior through one explicit
test-only 503, using original freshness limits and no human intervention.
Broader MAP-11 reconnect/restart/HA criteria remain open until separately
implemented and verified. No technical observation becomes production evidence.
