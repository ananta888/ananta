# Live Meet readiness observations

Run `python -m scripts.check_meet_live_readiness` for bounded, unauthenticated
GET observations of `/healthz`, `/config` and `/api/machine/capabilities` on
the configured HTTPS origin. `--container` selects one exact local Docker
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
