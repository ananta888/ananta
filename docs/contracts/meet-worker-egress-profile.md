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
