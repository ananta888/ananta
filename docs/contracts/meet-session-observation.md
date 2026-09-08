# Authoritative Meet session observation (MAP-09/12)

Source audit at Ananta `3bca53be3`, Meet `e0e4bfa`: current Hub status is the
TaskQueue status. The existing Meet authorization backchannel proves current
membership and permitted incoming audio/chat, not the machine's own publication
state. Worker controls and screenshots cannot substitute for that missing fact.

First add a separate closed `ananta.meet-session-observation.v1` backchannel at
`POST /api/machine/sessions/observation`. It takes the existing exact room,
session and nonce request, a fresh scoped Hub grant, and returns only bound
membership/lease metadata plus the current machine's own registered publications.
Human sources, receive grants, media, SDP, tokens and display names are excluded.
Own publications have a monotone per-membership revision, including source stop
and replacement; identical repeated media-state commands do not advance it.
The existing authorization endpoint and Worker wire remain shape-compatible.

The Hub validates exact schema, nonce, issuer/tenant/project/task/runtime/session/
capability binding, lease/deadline, membership and publication revisions, and
source-to-capability mapping. Its own task authority is checked before and after
the TLS request. Missing, malformed or obsolete endpoints fail closed, without
inferring publication from Worker assertions or falling back to weaker state.
The optional observation must not add a second task scheduler or auto-enable any
source. It is a prerequisite for persistent session phases, not their completion.

`publishing` in this contract means an active publication registered by the Meet
control plane; it never proves decoded media, audible output, E2EE or delivery.
Membership remains ephemeral in Meet. Hub persistence and revision handling
follow separately under exact Task/dispatch/runtime bindings. Local tests use
synthetic identities and model output, not production release evidence.

Use a pure bounded projection/validator and narrow transport composition (SRP,
ISP, DIP). Preserve current HTTP grants, OIDC/PoP/replay and capture protection.
Verify start/stop/replace/no-op revisions, unknown/cross-scope/replayed grants,
expired/revoked membership, publication ownership and source capabilities, then
the actual private Hub/Worker/Meet browser. Companion full check runs in a private
worktree; never overwrite the serving frontend build or operator trust.

## Verification (2026-09-08)

Implemented in the companion at `777f7ce`, with the Hub's separate validated
`observe` transport. Common lease/membership validation is shared with the old
authorization path; publication and receive contracts remain separate (SRP/ISP).
No extra TLS request is added to the Worker's one-second control exchange.

The final unique focused Hub regression passed 170 tests in 66.79 seconds.
The companion's isolated full check passed 639 frontend and 573 Node tests
(three Node skips); build, Go, security and configuration checks also passed.
External infrastructure gates remained explicit skips, not release evidence.
The old source-replacement overflow defect was reproduced against the verified
pre-change RoomRegistry blob: a rejected replacement deleted the existing
publication. Both counter limits are now checked before mutation, with regression
coverage for preserving the old source.

The actual private Hub/Worker/Meet browser passed in 33.26 seconds: moving screen,
two correlated chat answers, own source counts `[1, 0, 1]`, publication revisions
`[3, 4, 5]`, unchanged membership, and denial after Hub cancellation. Source
pause/resume converged in 845.61/1132.17 ms. Resumed decoding was not asserted.
The initial fixture allowed only 958 ms for a one-second control cadence. Its
separate deterministic wait helper now enforces a strict three-second success
budget and 32-observation cap; an in-flight HTTP call retains its own three-second
transport deadline. Seven timing cases include late success, frozen/backward and
non-finite clocks. No Worker stop deadline or product retry was relaxed.

The browser build was produced in a fresh private worktree; the preflight correctly
rejected the older source build. Serving files, trust and deployment were untouched.
All results are synthetic technical checks. Persistent phases and the intermittent
decoder-startup failure remain separate open work; this does not complete MAP-09.
