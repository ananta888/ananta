# Bounded dialog browser session operations

## Source check (MAP-11/29)

The delegated dialog runtime still awaits `join`, `renew` and `leave` Promises
inside Playwright `evaluate`. A page default timeout does not make arbitrary
JavaScript Promise settlement a Hub-authority checkpoint. The standalone media
publisher already uses a polled browser phase, but its 120-second lifetime and
publish operation are not the dialog's two-hour contract.

## Implementation scope

Share only the existing small browser phase scripts; preserve the standalone
publisher's API and budgets. Compose a separate dialog-session adapter with its
original monotonic assignment deadline, exact `/machine` URL and fixed operation
allowlist: join (20 seconds), renewal (at most 2.5 seconds and the current Hub
projection's remaining freshness), leave (3 seconds). Readiness is bounded by
20 seconds and checks the dialog methods, not unrelated media publication.

Start each operation exactly once, poll without awaiting its Promise, and check
the deadline/navigation before and after each browser RPC. During renewal all
owned sources and pending replies are invalidated first. Do not refresh cached
authority from a timer, consume or replay a renewal grant twice, extend a grant,
or retry failed/unknown settlement. A failed adapter is terminal; disposable
browser teardown prevents a late Promise from reviving an execution. After a
successful renewal require the existing fresh Hub exchange before any output.
Close sources before normal leave as well. Cleanup remains idempotent.

This is fail-closed bounded renewal, not reconnect or high-availability recovery.
Before join there is no established Hub control projection: the issued grant,
assignment deadline and 20-second setup bound apply. Browser renderer/transport
failure can still prevent individual RPCs from returning; the outer isolated
Worker deadline remains the final bound. Do not claim hard real-time guarantees.

Keep session operations separate from media pumps and Hub policy (SRP/ISP/DIP),
and reuse transport phase scripts instead of copying their settlement logic.
The runtime remains the owner of its assigned execution, never a second task
scheduler. Existing room, source, identity and wire contracts are unchanged.

## Verification

Deterministic clocks cover never-settling join/renew/leave, exact deadlines,
assignment expiry, stale control, navigation, invalid settlement and post-RPC
revocation. Execute the shared JavaScript to prove late settlement cannot replace
a newer phase and exceptions are redacted. Run existing standalone publication
and dialog control tests, then private real-browser chat/speech and actual lease
renewal. All tests are headless with synthetic policies; these checks do not mint
production evidence or change running deployments.
