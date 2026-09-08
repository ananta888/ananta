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
