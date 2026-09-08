# Fixed-endpoint Worker egress profile (MAP-10)

## Source audit and additive scope

At `2df574089`, the standalone media Compose template provides resource,
device, root-filesystem and process restrictions, but its bridge network has
no complete container-level egress filter. The fixed Meet HTTP/CSP adapter
and public-document fetch checks protect their application ports; neither is
a firewall for arbitrary process DNS, TCP, UDP or WebRTC ICE traffic.

Implement an additional explicit `fixed-endpoints-v1` deployment profile,
without changing the existing compatibility template or running deployment.
One minimal guard container owns a new private network namespace; exactly
one assigned Worker container shares that namespace. The guard is not a
Worker, Hub or task scheduler. It installs a closed operator-configured
IPv4 endpoint allowlist and default-deny IPv4/IPv6 filter in its own namespace.
Only the guard receives NET_ADMIN, never the Worker. No host networking,
host PID namespace, Docker socket, host firewall rule or public listener is
allowed. Preserve the ordinary Worker filesystem/device/CPU/memory limits.

The Hub retains all assignment, lease, policy and queue ownership. This is
an explicit execution-environment ceiling, not permission to publish or
receive. Existing HMAC, TLS, source, browser and headless policy checks remain
mandatory. The additive profile must not falsely advertise that the legacy
unfiltered deployment acquired network isolation.

## Closed configuration and DNS

A bounded immutable configuration names literal IPv4 destinations, exact
TCP/UDP ports, permitted Hub caller addresses for the existing 8094 listener,
and a small set of exact hostname-to-admitted-address mappings. No hostname
resolution during rule installation, CIDR/wildcard/range endpoints, arbitrary
commands, secrets, remote policy URLs or inferred defaults. Reject malformed,
duplicate, oversized or unsupported fields. IPv6 egress remains disabled in
this first explicit profile rather than bypassing the IPv4 policy.

A local, non-forwarding DNS responder serves only configured records and
bounded A/AAAA questions. Unknown/unsupported queries never reach an external
resolver. Docker's embedded resolver may retain local bridge service-name
discovery, but all resulting destinations still face the endpoint filter;
its sole configured upstream is the guard's local fixed-record responder.
Verify actual resolver behavior and forbidden-destination counters, not merely
the rule text. DNS is not an application-content/DLP or compromised-endpoint
guarantee. Public browser origins must be explicitly pinned in this profile.

Only configured TURN endpoints may carry relay traffic. Unconfigured direct
ICE peers must be blocked; do not expand a destination/port range to make a
test pass. Actual TURN operation is a separate acceptance step, not something
an HTTP-only firewall check can establish. Preserve any Docker-owned NAT/DNS
tables; policy installation targets the private filter table only. The Worker
starts only after successful guard readiness. No live policy mutation or
unbounded automatic recovery is added.

## Structure and verification

Separate pure configuration/rule generation, bounded DNS wire parsing,
namespace-local rule application and service lifecycle (SRP/DIP). Reuse the
existing pinned Python base for a small stdlib/iptables guard image, not the
GPU/LLM image. Configuration, resolver and enforcer failures remain closed.

First verify pure hostile configuration/DNS/rule cases and real local UDP/TCP
resolution. Then use only newly created, label-matched disposable containers
and an internal test bridge: permitted HTTP/UDP destinations must receive
traffic, forbidden ones must receive zero; IPv6 and external DNS must not
escape. Verify the actual namespace, capabilities, immutable configuration,
non-root Worker and missing host interfaces/socket mounts before injection.
Render the additive Compose overlay with a secret-free environment and test
readiness failure. Do not claim full MAP-10, public TURN or production release
evidence before the corresponding installed-runtime gates pass.

References: Docker documents the separate container network stack and its
unsupported per-consumer DNS/port flags in
[container networks](https://docs.docker.com/engine/network/#container-networks),
and distinguishes service/container sharing from host networking in
[Compose networking](https://docs.docker.com/compose/how-tos/networking/).
The [iptables-restore manual](https://man7.org/linux/man-pages/man8/iptables-restore.8.html)
defines filter restoration, test parsing and bounded lock waits.

## Deployment contract

The opt-in overlay is `docker-compose.meet-egress.yml`, applied **after**
`docker-compose.meet-media.yml`. It does not activate itself or change any
existing running service. Set `MEET_EGRESS_POLICY_FILE` to an absolute path to
a regular, non-symlink policy file before rendering or creating the profile.
The file is mounted read-only into the guard only; a missing host file must
not be silently created as a directory. Existing model/state/key mounts and
Worker GPU selection, seccomp, non-root user and resource limits are retained.

The configuration has exactly these fields (documentation-only addresses):

```json
{
  "schema": "ananta.meet-worker-egress.v1",
  "endpoints": [
    {"address": "192.0.2.10", "protocol": "tcp", "port": 443},
    {"address": "192.0.2.20", "protocol": "udp", "port": 3478}
  ],
  "names": [{"name": "meet.example.test", "address": "192.0.2.10"}],
  "hub_clients": ["192.0.2.30"]
}
```

Declare every necessary Hub callback, Meet TLS, model provider, public-document
and TURN transport endpoint explicitly. `hub_clients` permits only incoming
TCP to the existing 8094 Worker listener; it does not allow outbound callbacks.
Pin local service addresses in an operator-owned IPAM/static-address overlay
and use matching policy records. An automatically changing service address is
not an automatically admitted replacement. DNS records must point to an
admitted endpoint address. External ports 53/853, IPv6, address ranges and
wildcard hostnames are not supported by this profile. TURN relay allocation
ports belong on the TURN server's side, not in a wildcard Worker allowlist.

The guard retains only NET_ADMIN and NET_BIND_SERVICE, a 64 MiB memory budget,
16 PIDs, 0.25 CPU and two tiny private tmpfs paths. Its fixed commands change
only the private filter tables; each command has a two-second lock wait and
four-second wall-clock bound. Configuration validation, both-family rule
validation/installation and DNS binding must succeed before readiness is
written. The readiness probe binds the immutable policy digest to a real,
half-second loopback DNS query. No Task, lease, grant or model is touched.

The guard must own a **private** namespace and serve exactly one Worker.
Never override its network/PID mode with host sharing or attach other
consumers. The container marker/PID check only prevents accidental direct
host invocation; it cannot prove that a malicious Docker invocation did not
select host networking. Deployment tools must enforce that boundary.
Never run the enforcer module against the host firewall.

There is no policy reload, guard restart loop or automatic fail-open mode.
Changing a policy requires a new, verified guard/Worker pair. Health failure
does not grant restart, reassignment or new admission authority. A policy digest
is configuration metadata, **not** a Hub evidence identifier. The original
unfiltered Compose profile remains available for compatibility and must not
be represented as the strict profile.

## Verification boundaries

The isolated gate `tests/test_meet_egress_containers.py` requires explicit
`MEET_EGRESS_GATE=1` and an immutable locally built `MEET_EGRESS_IMAGE`.
It creates its own label-bound internal bridge and temporary containers,
checks exact resource IDs before cleanup, and does not contact public hosts.
Actual counter-bearing HTTP/UDP endpoints distinguish delivery from merely
observing a connection error; fixed DNS is checked through Docker's embedded
resolver. Invalid configuration must never start a namespace consumer.

This transport probe is not a full packaged media Worker, a TURN session or
production evidence. Those acceptance steps remain separate. The service
emits only one of five fixed failure phases (configuration, filter, DNS bind,
readiness, DNS service), never raw command output, queries or policy contents.
The enforcer and resolver remain injectable, separate responsibilities (SRP/DIP);
no Hub dependencies or independent Worker orchestration are introduced.

### Installed transport observation (2026-09-09)

The guard image built from `baabf4229` is
`sha256:896c817caac38f09c263c86cf2113db597d19f549bd93067dbce420c6aa017ef`.
Two actual container gates passed in 34.11 seconds: invalid policy terminated
without starting a consumer; valid policy admitted only configured HTTP/UDP
and the authorized Hub caller. A live second TCP port on the admitted address,
an unadmitted endpoint, external DNS listeners and a live IPv6 loopback UDP
listener received zero forbidden requests. Docker's actual embedded UDP and
TCP DNS returned the pinned A record and NXDOMAIN for an unknown name.
All temporary containers/bridge were removed by exact label-checked IDs.

The focused suite passed 122 tests in 54.24 seconds, including real Compose
merges, hostile policy/DNS input, slow TCP clients and lifecycle failures.
After adding closed failure-phase diagnostics, 24 lifecycle/probe tests also
passed. Initial gate errors were test defects: capability-prefix comparison,
an HTTP helper/import name collision, and a helper dependency missing from
the script module path. The last made a stopped endpoint contribute an empty
address; the guard correctly rejected that policy. Explicit endpoint readiness
and address validation now precede guard construction. No firewall rule,
timeout, capability ceiling or policy check was weakened to fix these tests.
These observations are synthetic transport checks, not production evidence.
