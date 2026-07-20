# Optional semantic-media SFU operations

The SFU is an optional edge transport. It never becomes the Ananta control
plane: the Hub owns room admission, membership epochs, publication and
subscription grants. Workers are not attached to the SFU network.

## Start, verify and stop

Use a random API key and a secret of at least 32 bytes. Keep both out of Git.

```bash
ANANTA_SEMANTIC_MEDIA_SFU_API_KEY=... \
ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET=... \
docker compose -f docker-compose.semantic-media.yml \
  --profile semantic-media-sfu up -d
scripts/semantic-media-sfu-healthcheck.sh
docker compose -f docker-compose.semantic-media.yml \
  --profile semantic-media-sfu down
```

The disabled state requires no container and leaves the ordinary peer-to-peer
WebRTC/data-channel path unchanged. Readiness is the HTTP health check plus a
successful Hub-issued, short-lived join. Before maintenance, stop issuing new
admissions, wait at most the configured 30-second grace period, then stop the
container. Clients deterministically fall back to ordinary WebRTC; they must
not keep a second bulk media path alive.

Expose UDP 7882, TCP 7881 and the embedded TURN/UDP port 3478 only through the
intended edge firewall. The checked-in configuration enables TURN/UDP so the
release gate can force and capture a real relay path. For internet deployments,
configure a public node address and trusted TURN/TLS certificates before
admitting non-loopback clients; the local loopback configuration is
intentionally not a production NAT recipe. Monitor ingress/egress, packet loss,
RTT, jitter, CPU, memory, reconnects and fallbacks without media content or
candidate addresses.

The group E2E runner captures direct RTC traffic and TURN traffic in separate
temporary PCAP files inside the respective LiveKit and TURN network namespaces. It requires an
actual relay candidate in Chromium and Firefox, probes known plaintext, records
only bounded counters and deletes the captures when the run exits.
For the loopback-only relay proof it starts the separately profiled, digest-
pinned `semantic-media-turn-gate` container on the same isolated edge network.
Its public fixture credential is intentionally limited to that disposable gate
and is not a production credential or an alternative admission authority.

## Destructive local failover gate

Run the destructive browser gate only on a Docker-capable development or CI
host. The runner creates a random Compose project and a temporary LiveKit
configuration under `/tmp`; HTTP, RTC/TCP and RTC/UDP use matching dynamic
host/container ports so advertised loopback ICE candidates remain valid. It
also starts a loopback Hub composition against a temporary SQL database and
never issues lifecycle commands against a normal Hub or SFU project.

```bash
python scripts/e2e/semantic_sfu_failover_e2e.py
python scripts/run_semantic_sfu_gate.py
python scripts/run_semantic_media_chaos_gate.py --execute \
  --output artifacts/test-gates/semantic-media-chaos.json
```

For both Chromium and Firefox, the gate requires one publisher, two regular
subscribers and a stale-key probe to receive real encrypted RTP before it sends
`SIGKILL` to its own LiveKit container. Every browser must enter a controlled
disconnect, the two regular subscribers must receive bytes over direct WebRTC
ordinary-audio fallback connections, and no semantic SFU room may remain
active during fallback. While that fallback stays available, the runner also
sends `SIGKILL` to the isolated Hub and restarts it against the same database.
After restart, the browsers accept only a fresh
Ed25519-signed `hub_failover` epoch. The old authorization is rejected, the old
key receives encrypted bytes but decodes no samples, and the fresh key restores
both regular subscribers.

Admission tokens and signed epochs come from the productive
`semantic_sfu_admission` API/service/repository composition; the browser spike
contains no Hub signer or LiveKit-token minting code. The productive semantic
compute API must resume the persisted pre-failure primary/validator leases,
fence a failed primary, issue a monotonically newer replacement and reduce two
concurrent validator schedules to one winner plus one `lease_overlap`. Exactly
one primary and one validator may remain active for that conflict scope.
Workers never orchestrate this flow and no second control plane is attached to
the SFU.
Successful, content-free measurements are written to
`artifacts/domain/semantic-sfu-live-failover.json`; the SFU and chaos gates
recompute that evidence and set `external_live_failover_verified` only when
container, browser, track, worker and Compose-project cleanup all pass.
