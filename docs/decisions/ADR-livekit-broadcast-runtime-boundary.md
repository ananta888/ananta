# ADR: LiveKit broadcast runtime boundary

Status: accepted for feasibility, activation blocked without current runtime evidence (2026-07-22)

## Decision

Ananta uses the pinned stock LiveKit runtime through documented public APIs.
The binding is:

| Decision key | Selected value |
| --- | --- |
| runtime_control_mode | livekit_control_api |
| placement_owner | livekit_native |
| server | LiveKit Server 1.13.1 |
| server image | livekit/livekit-server@sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3 |
| browser SDK | livekit-client 2.20.1 |
| server SDK | not used; the documented Twirp JSON boundary is called directly |

There is exactly one media-runtime control mode. An authenticated runtime
extension is not selected, and private LiveKit hooks, reflected fields and
server-internal packages are outside the supported boundary. The Hub remains
the sole Ananta control plane and owns audience policy, revisions, epochs,
admission and reconciliation. LiveKit owns room placement. The Hub must not
select a competing LiveKit node or set CreateRoom.node_id.

The broadcast feature remains default false until the runtime probe passes
against the exact image, client package lock and mounted configuration. A
static API declaration, a unit mock or a previously written JSON report cannot
make a capability available.

## Public capability boundary

The selected control surface is LiveKit RoomService over its documented Twirp
JSON endpoints. The feasibility smoke uses ListParticipants to observe the
real room and then exercises UpdateSubscriptions, UpdateParticipant and
SendData while one real browser publisher and three real browser receivers are
connected. The same run verifies one publisher upload, three decoded receiver
flows and browser E2EE against the running container.

Publisher-side setTrackSubscriptionPermissions remains defense in depth. It
does not replace Hub authorization or the server-side subscription command.
Custom JWT claims are opaque application metadata and are not evidence of
server-side audience enforcement.

The following limitations are intentional and fail closed:

- Route epochs, queue fencing and Ananta command acknowledgements are not
  native LiveKit guarantees. They stay Hub-owned and are not invented by a
  Python adapter.
- The checked-in single-node configuration has no Redis-backed distributed
  placement. If distributed mode is introduced, LiveKit remains its placement
  owner.
- Prometheus metrics are unsupported until prometheus_port is explicitly
  configured and probed.
- TURN is configured, but is only documented until a relay-selected real run
  supplies evidence.
- Reliable data payloads are limited by policy to 15 KiB and lossy payloads to
  1300 bytes. The current 16 KiB client guard is therefore degraded rather
  than treated as an available safety boundary.
- Drain is a documented native SIGTERM/SIGINT/SIGQUIT behavior. A static
  Compose grace period is not live drain evidence.

## Evidence and operation

The deterministic fail-closed baseline is generated without runtime access:

    python scripts/probe_livekit_broadcast_runtime.py

It writes artifacts/domain/livekit-broadcast-runtime-capabilities.json and
returns nonzero because runtime evidence is absent. On a dedicated host with
Docker, installed pinned frontend dependencies and Chromium:

    python scripts/probe_livekit_broadcast_runtime.py --execute-runtime

The executable mode owns a random Compose project, verifies the running
container image reference, server version and mounted-config digest, starts
the existing browser spike with three receivers, invokes only documented
RoomService endpoints during that session, validates the content-free report
and tears the project down. Version drift, missing runtime evidence, mock-only
evidence, API failure, topology mismatch or cleanup failure returns nonzero.
The report contains hashes and bounded counters, not tokens, payloads or media.

No SRC_* or RUN_* identifier is created by this decision. External references
are recorded as URLs and are not represented as project source attestations:

- https://docs.livekit.io/reference/other/roomservice-api/
- https://docs.livekit.io/transport/data/packets/
- https://docs.livekit.io/transport/encryption/
- https://docs.livekit.io/transport/self-hosting/distributed/
- https://docs.livekit.io/transport/self-hosting/deployment/

## SOLID check

The probe separates static binding discovery, runtime execution, report
evaluation and CLI persistence. This protects SRP. Runtime execution is behind
one narrow observation type and the report evaluator depends on that value
rather than Docker subprocesses, protecting DIP and testability. Capability
rows are additive, protecting OCP. No broad adapter or hidden side effect is
introduced into Hub or Worker production code.

