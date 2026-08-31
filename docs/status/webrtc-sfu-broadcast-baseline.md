# WebRTC/SFU broadcast baseline

This status document is the human-readable companion to
`artifacts/domain/webrtc-sfu-broadcast-baseline.json`. It describes source tree
state, not planned state. Paths named only by a todo are never counted as an
implementation. No `SRC_*` or `RUN_*` identifier is asserted.

## Reproducibility

- Audit tool: `scripts/audit_webrtc_sfu_broadcast_baseline.py` version `1.0.0`
- Source commit: `3116c45a3b46e98c45b7e6f9127bbbc6199f5fa0`
- `config/livekit.semantic-media.yaml`: `666239e38bb11cac71cbb211bfdb8301525b5335d917cc1c36b062aad29a682b`
- `docker-compose.semantic-media.yml`: `5189915ad7529b857c645169d1b9ef7ee3aee316e1e3c8f7b1c7586c6c308a1e`
- `frontend-angular/package.json`: `4734282a76a787f901f5579d098b0daae75952e5e5ad25f807db3f1b2e3735c1`

The tool sorts findings and JSON keys, emits no timestamp or random value, and
accepts `--root` for deterministic fixture trees. Missing mandatory paths,
changed absence claims and a parent decision other than the expected
`no_go/observe_only` return a nonzero exit. `--check` compares byte-for-byte
against the tracked artifact.

## Decision summary

The Parent program is complete but its referenced readiness decision remains
`no_go` and `observe_only`. Therefore audit, contracts and flag-off feasibility
work may continue; canary, receiver-cap activation and release remain blocked.
The repository path for that claim is
`todos/active/todo.webrtc-sfu-broadcast-fanout.json`; its referenced evidence path is
`artifacts/test-gates/semantic-media-program-evidence.json`.

## Inventory

| Area | Status | Repository evidence | Finding |
| --- | --- | --- | --- |
| Pair media | present | `frontend-angular/src/app/services/webrtc-media-session.service.ts` | Productive browser capture and peer media session exist. |
| Relay | present | `agent/repositories/semantic_relay_shared_store.py` | Encrypted relay state uses a shared relational repository. |
| Signaling | present | `agent/routes/webrtc_signaling.py` | Signaling remains a Hub route boundary. |
| Media contracts | present | `schemas/webrtc/media_publication.v1.json`, `schemas/webrtc/media_subscription.v1.json` | Audience, participant, epoch and revision are explicit. |
| Contract persistence | present | `migrations/versions/c7d8e9f0a1b2_add_semantic_media_contracts.py` | Publication/subscription persistence is migrated. |
| Admission persistence | present | `migrations/versions/e6f7a8b9c0d1_add_semantic_sfu_admission_state.py` | Hub SFU admission state is migrated. |
| Hub admission | present | `agent/services/semantic_sfu_admission_service.py` | Membership/revision checks and narrowed grants exist. |
| LiveKit browser SDK | present | `frontend-angular/package.json` | `livekit-client` is pinned to `2.20.1`. |
| Productive E2EE | present | `frontend-angular/src/app/services/livekit-sfu-room.adapter.ts` | The room adapter uses `BaseKeyProvider`. |
| Custom frame helper | partial | `frontend-angular/src/app/services/sfu-media-frame-crypto.service.ts` | `SfuMediaFrameCryptoService` is referenced by its spec, not productive consumers. |
| Publisher audience | present | `frontend-angular/src/app/services/livekit-sfu-room.adapter.ts` | Default-deny `setTrackSubscriptionPermissions` projects Hub-authorized receiver IDs. |
| Room/group cap | present | `agent/services/sfu_broadcast_participant_limits.py` | Hard limit is 8 participants. |
| Publication audience cap | present | `frontend-angular/src/app/services/sfu-broadcast-limits.ts` | Hard limit is 7 receivers. |
| LiveKit container | present | `docker-compose.semantic-media.yml` | Pinned, isolated, profile-gated server exists. |
| TURN | partial | `docker-compose.semantic-media.yml`, `config/livekit.semantic-media.yaml` | Embedded TURN and a test gate exist; no regional pool is established. |
| Server SDK / node agent | missing | `requirements.txt`, `pyproject.toml` | No Hub-side LiveKit server SDK or authenticated node agent is declared. |
| Fleet control | missing | `docker-compose.semantic-media.yml` | The active Compose topology has no Redis-backed broadcast fleet. |
| Three-peer evidence | partial | `tests/e2e/test_semantic_sfu_spike.py` | One publisher plus two receivers is covered, not broadcast capacity. |
| Failover evidence | partial | `tests/chaos/test_semantic_sfu_failover.py` | Parent fallback coverage exists, not a selected broadcast runtime boundary. |
| Observability | partial | `docs/privacy/semantic-media-observability.md` | Content-safe rules exist; fleet/node metrics remain follow-up work. |
| Operations | present | `docs/operations/semantic-media-sfu.md` | Opt-in lifecycle and fallback are documented. |

## Consequence

The current implementation is a bounded Pair/group foundation, not a release
claim for broadcast fan-out. The 7/8 values are hard safety caps, not measured
capacity. Stock LiveKit can be used only through the documented public control
boundary; stronger route fencing or node acknowledgement requires a separately
selected authenticated runtime extension. Until that selection and green gate
evidence exist, the effective mode is `unsupported` for broadcast activation.

## SOLID check

The audit separates probe data, evidence validation, source provenance and CLI
serialization (SRP). The root is injected for fixtures and no service locator is
used (DIP). Runtime-specific capabilities are represented as explicit modes
rather than no-op implementations (LSP). No broad worker or SFU orchestration
interface is introduced (ISP), and new probes are additive records (OCP).
