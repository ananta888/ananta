# Live Meet readiness observations

Run `python -m scripts.check_meet_live_readiness` for bounded, unauthenticated
GET observations of `/healthz`, `/config` and `/api/machine/capabilities` on
the configured HTTPS origin, plus the additive `/api/machine/integration`
observation. `--container` selects one exact local Docker
container for a read-only running/image/revision observation. This association
does not itself prove that the container is the public reverse proxy's upstream.

No trust, room, token, login, project policy, deployment or container state is
created or modified. Curl configuration/proxies/redirects are disabled, TLS
verification remains enabled, each request has a ten-second total bound and a
16-KiB body limit; the parent additionally bounds each command. Docker inspection
requests only three selected fields, never environment values, mounts or
process arguments. Reports project booleans, bounded occupancy numbers and
validated hashes; public ICE credentials and authentication metadata are not
printed. Malformed/duplicate/oversize JSON and command failures are unavailable,
not invented readiness. Standard input is closed throughout.

The explicit `--local-tls-route` diagnostic resolves only the origin's TLS
virtual host to loopback and labels the result `local-tls-hairpin`. It does not
claim public DNS, Internet routing, NAT traversal or an independent external
receiver. Ordinary host-DNS HTTPS success also is not a multi-host media test.
`observed` means only that these limited public checks passed; exact Hub trust
scope/key, current project preauthorization, relay-selected payload and long
dialog acceptance remain explicitly unverified. No result grants production
release eligibility. Non-passing observations return machine-readable blocked
JSON and exit two, never wait for an operator.

Read-only transport/JSON limits, pure content-free report projection and CLI
composition remain separate responsibilities (SRP). Existing Meet admission,
Hub task/policy authority and the companion's local operator-profile preflight
are reused as separate boundaries; this is not another authorization engine.

## Additive integration observation

The optional `integration` report section validates the entire closed
`ananta.meet-integration.v1` contract introduced in Meet `f67c9e5`. Fixed
implemented capabilities, the sorted unique global operator ceiling, mandatory
publisher-consent kinds and the exact session-lease version are separate fields.
Unknown fields/versions, wrong types, missing entries, duplicate/unsorted/unknown
capabilities or an enabled admission with an empty ceiling are `unavailable`.
Disagreement with the separately fetched legacy admission snapshot is
`inconsistent`; no positive metadata is retained. This may be a deployment race,
not proof of a specific server defect. Unknown values are null, not an invented
empty capability set. Each invocation fetches afresh and follows no redirects.

This informational section does not alter the old report status or legacy
capability semantics. In particular `observed` remains a limited observation,
not comprehensive readiness. Old servers without the additive endpoint continue
to report their legacy checks, with integration unavailable. Neither a global
ceiling nor implemented software grants a Hub key, project, task or publisher
right. No deployment, login or trust configuration is performed.

The pure version adapter in `scripts/meet_integration_observation.py` is separate
from command transport and CLI orchestration (SRP/DIP). It owns a small explicit
versioned compatibility projection rather than importing companion runtime code.
70 focused readiness/negative/CLI tests passed in 50.17 seconds; five real
companion HTTP/schema tests passed in 0.431 seconds on `5a10338`. Three actual
Node-produced full/empty/chat-only projections were also accepted by the Python
adapter. These are synthetic technical checks, not public authorization evidence.

The actual public read-only repeat at 19:28 Europe/Berlin on 2026-09-09 showed
the older running revision below, required auth/SFrame and configured TURN,
zero occupancy, disabled machine admission and unavailable additive integration.
GitHub contains named TUI test secrets in a separate environment, but the
existing workflow defaults to realm `ananta-e2e`, unlike public Meet's `ananta`.
Their suitability is unverified; values were not retrieved and the unrelated
workflow was not dispatched.

## Actual instance checkpoint

On 2026-09-09 the loopback TLS route reported healthy, required human auth and
required SFrame, TURN configured, zero rooms/participants, but machine admission
disabled. The inspected running container reported revision
`92f583f34680cbf371a83872512cd9333cc41881`; it is not the current companion
checkout. An earlier ordinary DNS request timed out, so public DNS reachability
was not inferred from the working local TLS path. The runtime and trust were
not changed by these observations. A scoped operator trust configuration and
separately verified deployment are needed before the public machine test can
join; disabling authentication is not an alternative.

The later host-DNS HTTPS checks, most recently during the 15:43 Europe/Berlin
checkpoint on the same day, succeeded for health/auth/SFrame/TURN. The earlier
DNS timeout is historical, not the current blocker. Machine admission remained
disabled, occupancy was zero, and the inspected image/revision remained the
same older deployment. Neither successful HTTPS nor this empty-room snapshot
authorizes a new Hub trust scope or identifies an OIDC test account.
