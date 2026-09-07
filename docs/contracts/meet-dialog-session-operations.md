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
projection's remaining freshness), leave (3 seconds). Leave alone retains that
cleanup budget after assignment expiry; it cannot create or extend authority.
Readiness is bounded by
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

## Technical verification

Implemented with one shared browser-phase transport and a separate narrow dialog
adapter. Standalone publication still has its original operations and 120-second
lifetime; a dialog can renew after ten minutes without inheriting that cap.
The fresh-control checkpoint performs no IO or authority extension. Browser and
source cleanup order is tested separately from Promise settlement (SRP/DIP);
the runtime's existing multi-source composition responsibility remains unchanged.

All 59 deterministic session/control/standalone-publication tests passed in
29.84 seconds, including the exact shared JavaScript's late-settlement and
redaction cases. Four runtime composition/cleanup tests passed in 8.95 seconds.
Two real private-browser cases passed together in 117.74 seconds: ordinary
chat/screen and an actual lease renewal with persona image and spoken replies.
The adjacent Meet source revision was
`c20f4533308ee783807af7c9396f5b51fea965a1`; its existing local frontend build was
used, not rebuilt or deployed by this change. Media/policies remain synthetic,
and no GPU, public TURN, multi-host or production release claim follows.
Targeted Ruff lint/format and whitespace checks passed.
