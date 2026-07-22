# SFU broadcast TURN deployment

This deployment is an optional relay data plane controlled by the Ananta hub.
Coturn and its observer do not own task routing, policy, credentials, capacity
admission, or the pool directory.  A collector can only submit signed,
aggregate observations for its enrolled pool instance.

## Fail-closed defaults

`docker-compose.sfu-broadcast-turn.yml` creates no container unless the
`sfu_broadcast_turn` profile is explicitly enabled.  It additionally requires
an immutable Coturn image reference, an immutable signed observer image,
real TLS/observer secrets, public addressing, registered IDs and writable
observer state directories owned by UID/GID `65532`.  The checked-in trust and
export policies are `no_go`; empty CA/evidence lists are intentional.

Coturn's official configuration reference documents REST shared-secret
authentication, quotas, relay bandwidth controls, lifetimes and the local
Prometheus endpoint:

- <https://github.com/coturn/coturn/blob/master/README.turnserver>
- <https://github.com/coturn/coturn/blob/master/examples/etc/turnserver.conf>

The Compose contract names the versioned Coturn tag but does not provide a
digest.  Resolve and review the vendor-published digest for the target
architecture, scan it, then supply the complete `tag@sha256:digest` value.  A
tag alone is not an approved production input.  Build the observer image from
`docker/turn-observer-agent.Dockerfile` with a digest-pinned Python base, scan
and sign it, and supply its immutable registry digest to Compose.

## External prerequisites

Provision two independently failure-contained hosts or schedulers for real
redundancy.  The single-host Compose mapping is a development deployment
contract, not an availability claim.  Configure public DNS and certificates,
NAT one-to-one mappings where applicable, host/cloud firewalls for TURN UDP/TCP
3478/3479, TURN TLS 5349/5350 and the declared UDP relay ranges.  Do not expose
port 9641, a Coturn CLI/admin port, container management sockets, or observer
state.

Create separate REST-auth, TLS, observer signing, observer mTLS and observer-CA
secrets.  Enroll each observer through the administrator API using proof of
possession, record its returned identity version, then register the matching
pool node/config digest in the hub.  Pre-create each state directory with mode
0700 and ownership `65532:65532`; never share it between instances.

## Activation sequence

1. Verify image digests, signatures/SBOMs and host firewall rules.
2. Enroll distinct observer identities and register distinct pool instances.
3. Start with `--profile sfu_broadcast_turn` in a non-production environment.
4. From an external network, allocate and relay traffic over UDP, TCP and TLS;
   a loopback STUN healthcheck alone is insufficient.
5. Verify that observations are signed, fresh, monotonically sequenced and
   projected as healthy without exposing usernames, IPs or session IDs.
6. Exercise instance loss, stale observations, config mismatch, identity
   rotation/revoke, quota exhaustion and bounded client failover.
7. Attach only genuine `SRC_*`/`RUN_*` evidence and its artifact digest before
   changing an activation policy from `no_go`.

Emergency revocation must first revoke the observer identity in durable hub
state.  The directory then marks every node for that identity `capacity=stop`;
removing containers or certificates is defense in depth, not the authoritative
control-plane action.
