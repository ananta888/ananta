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

## Implemented and verified slice

Separate `dialog_control_transport` and `dialog_control_retry` modules now
classify fixed transport failures and reserve bounded replacement reads. The
existing signed client closes unread HTTP-error bodies, returns only the
closed unavailable signal for eligible `exchange` failures, and retains
terminal behavior for every other action and all trust/contract denials.
The scheduler keeps one future and never advances `fresh_until` on a retry.
During recovery, even a valid but obsolete response consumes the remaining
replacement-read budget and backoff; it cannot create an unlimited sequence
of superficially successful reads. Existing terminal diagnostics map exhausted
recovery into their established content-free Hub-unavailable reason.

Verification: **72 initial unit/legacy tests in 38.12 s**, **25 signed HTTP and
diagnostic tests in 19.20 s**, then **94 final combined regressions in 46.61 s**.
The private actual Hub/Worker/Meet scenario passed in **43.47 s** through one
explicit second-read HTTP 503: a subsequent signed current state arrives,
normal chat/screen exchange continues, source pause/stop and terminal cleanup
still work. It uses current Python source with the immutable sandboxed browser
image, explicit synthetic policy/model and private test infrastructure. It is
not installed-Worker, public outage, durable restart, GPU or production proof.

SOLID review: classification does not make policy decisions, retry accounting
has no transport or media dependencies, and scheduling cannot mint or extend
a lease. No Task dispatch, chat/audio result, grant redemption, navigation,
approval decision or publication operation acquired a retry path. Broader
MAP-11 recovery criteria remain open.

The installed-Worker gate now also passed in **44.85 s**, using immutable
image `sha256:db7d7af1bf7d2ad20e74f58ba22291ae532ad0f04185dadc0b58a3b271a6c20d`
built from `6c3c627e3`. Two separate role-assigned containers each encounter
one test-owned second-read HTTP 503 and recover through a fresh signed native
Hub response. Normal screen delivery, independent stops and terminal
observations remain checked. No application-source bind mounts, source/client
patches, relaxed freshness limits or retries of writes are involved. Three
fixture isolation tests passed in **8.41 s**. The fixture injects only those
two transport failures; its policy and infrastructure remain synthetic/private.
This proves installed read recovery, not public outages, room rejoin, durable
Hub restart, Worker replacement, GPU readiness or production release evidence.
