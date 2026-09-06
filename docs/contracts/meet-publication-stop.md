# Lease-polled legacy Meet publication

The existing `/machine` publication adapter previously awaited the page's
`join()` Promise directly in a synchronous Playwright RPC. A pending join
could suspend Hub lease polling until the outer worker deadline. Leave had
the same shape. The new `PublicationSession` starts each operation without
awaiting its Promise and polls only a content-free pending/done/failed state.

Readiness and join each allow at most 20 seconds, publication at most 85
seconds (the existing Meet API has two setup waits plus video playback), and
leave at most 3 seconds. Every phase is additionally capped by the original
Hub turn deadline, mapped once to a monotonic clock and never extended.
Ready/pending polls are at most 250 ms apart. The actual Hub callback retains
its separate bounded three-second HTTP validation. Authority and exact page
URL are checked before/after each operation observation; the in-page dispatch
also checks its URL before handing a grant or media to the API.

An expired/revoked/unavailable lease, navigation change, rejected operation or
unknown result fails closed. There is no retry, lease renewal, implicit
rejoin or second publication. Each Promise callback is scoped to its own
phase object and cannot overwrite a newer phase. Browser/context ownership
and cleanup stay in `publisher.py`; the session knows only a narrow page
port, lease checkpoint and injectable clocks (SRP/DIP). MP4 file handoff is
also bounded to the fixed media profile before joining.

The complete synchronous browser process remains subject to the existing
`TurnExecutor` process-group kill at the original task deadline. Navigation
has a five-second RPC budget. The polling bound is **not** a worst-case
network-to-receiver stop guarantee: a stalled renderer/Playwright RPC or
unavailable Hub callback can delay observation. No claim of a 250 ms total
stop SLA is made. A future long-lived MDS adapter still needs independent
publication generation fencing, transport renewal and receiver-side stop
verification; this change only hardens the existing bounded turn.

Unit tests use virtual time for never-resolving operations, phase deadlines,
lease revocation, changed pages, stale success and invalid state. The opt-in
`MEET_BROWSER_PROBE_GATE=1` test also runs real sandboxed Chromium with
in-process, intentionally unresolved join/publish/leave test doubles. All
requests are fulfilled inside its disposable browser context; there is no
public Meet connection, real credential, human device or approval. It is
synthetic local technical observation, not a receiver or release gate.

Reference results: 90 publication/media/lease/HTTP regressions passed in 63.21
seconds; another 38 checks including both real browser probes passed in 35.33
seconds. The initial actual Chromium pending-Promise probe observed stop after
509 ms (join), 510 ms (publish) and 507 ms (leave), including its intentional
400 ms pre-revocation interval. Total probe time was 2038 ms. Private worker
image: `sha256:b2a42d560e3a8f7f1dd72d7d7bf2ca9b53580a51906c13f91066148c59e7be53`.
