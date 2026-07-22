# SFU broadcast adapter capabilities

## Scope

`SFB-QOS-001` defines the Hub-side, vendor-neutral support boundary between the
runtime evidence produced by `SFB-BASE-006` and broadcast feature/adaptor
activation. It does not add an SFU data path, select an SFU node, or weaken the
Hub-owned task and policy model.

The only accepted input is an already-read primitive BASE-006 document. The
adapter retains no SDK object, vendor exception, credential, proprietary token,
documentation URL, or raw vendor reason. Domain callers see frozen models,
small capability-specific ports, and stable Ananta reason codes.

## Capability matrix

The contract distinguishes these capabilities instead of treating a generic
"LiveKit supported" result as sufficient:

| Domain capability | BASE-006 row | Version boundary | Required facts |
|---|---|---|---|
| Codec | `codec` | browser SDK | normalized codec set |
| Simulcast | `simulcast` | browser SDK | independent evidence |
| SVC | `svc_mode` | browser SDK | normalized scalability modes |
| Encoded transform | `encoded_transform_compatibility` | browser SDK | `e2ee_compatible=true` |
| Server subscription | `server_subscription_control` or `room_service_update_subscriptions` | server runtime | server-side control evidence |
| Data packet | `data_packet` or `data_packet_limits` | server and browser SDK | reliable and lossy positive limits |
| Data stream | `data_stream` | server and browser SDK | independent stream evidence |
| Queue hook | `queue_hook` | server runtime | authenticated and fenced |
| Egress metrics | `egress_metrics` | server runtime | independent egress evidence |
| TURN | `turn` or `embedded_turn` | server runtime | runtime evidence |
| Drain | `drain` or `native_drain` | server runtime | runtime evidence |

Legacy combined or weaker BASE-006 rows are references, not proof of stronger
claims. `simulcast_svc_track_publish_options` can therefore describe the two
separate domain entries only as `degraded`. `prometheus_metrics` is not promoted
to egress visibility, and route/epoch fencing is not promoted to an
authenticated queue hook.

## Evidence and version rules

The public status set is fixed to `available`, `degraded`, and `unsupported`.
An `available` result requires all of the following:

- exact BASE-006 schema and gate ID;
- BASE-006 decision `go`;
- an observed version inside every declared inclusive boundary;
- capability-specific required facts;
- at least one `SRC_*` or `RUN_*` identifier present in the capability evidence
  and independently supplied to the adapter as a known identifier.

The adapter never derives or invents a source identifier from a URL, digest,
path, SDK object, or free text. A digest is retained only as a binding reference
to BASE-006. Unknown or absent IDs are unverified. Missing rows and malformed or
duplicate rows are `unsupported`; documented, combined, blocked, versionless,
or ungrounded claims are at most `degraded`.

The repository's current BASE-006 artifact is deliberately consumed
fail-closed: its decision is blocked and it contains no valid runtime evidence
identifier. This contract does not reinterpret that artifact as activation
evidence. Real three-receiver proof remains owned by `SFB-BASE-006` and the
release gate `SFB-GATE-004`.

## Flags and adapter paths

`CapabilitySupportGate` is the shared Hub policy for flags and adapter entry
points. A requested flag is true only when every declared capability is
`available`; `degraded`, `unsupported`, unknown flags, non-boolean requests,
and storage/parser failures resolve false.

Adapter paths declare a small immutable `AdapterPathRequirement`. An empty
requirement cannot open a path. A path marked `carries_media` automatically
requires `encoded_transform`; callers cannot omit that dependency to obtain an
unencrypted fallback. Consequently absent transform compatibility never causes
an E2EE downgrade.

## SOLID boundary

- **SRP:** immutable models describe support, the BASE-006 adapter translates
  evidence, and `CapabilitySupportGate` decides activation.
- **OCP:** a new runtime adapter implements the existing ports without changing
  domain policies; new capabilities can be added explicitly rather than hidden
  in a generic vendor bag.
- **LSP:** every adapter returns the same stable statuses and reason codes;
  unsupported behavior is explicit rather than a no-op implementation.
- **ISP:** codec, simulcast, SVC, transform, subscription, packet, stream,
  queue, metrics, TURN, and drain each have a focused read port.
- **DIP:** Hub policy depends on `SfuBroadcastCapabilitySnapshotPort`, never on
  a LiveKit SDK, container object, or exception hierarchy.

There is no preserved or newly introduced worker orchestration: workers remain
execution-only, and the Hub remains the sole policy and activation authority.
