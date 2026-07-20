# Semantic media and speech threat model

This model covers Pair browsers, the Hub control plane, rendezvous/relay, optional SFU, Voice Runtime, reconciliation workers and speech-training workers. Default state is feature-off and deny.

## Assets and adversaries

Protected assets are media, transcripts, residual features, evidence, dataset membership, checkpoints, adapters, consent/permission state and cryptographic keys. Relevant adversaries include an unauthorized peer, a replaying former participant, a compromised transport, a stale worker attempt, a malicious training contribution, prompt injection and an operator mistake. Relay and SFU are honest-but-curious transports and receive ciphertext only.

## Required controls

| Threat | Required prevention | Detection/recovery |
|---|---|---|
| Permission-name drift | one versioned canonical permission contract; closed parsing | conformance gate blocks deployment |
| Plaintext marker presented as E2EE | Web Crypto AEAD, authenticated envelope and key epoch | tamper/replay tests and immediate session failure |
| Replay or stale participant | signed session, participant epoch, sequence window, nonce | stable stale/replay reason code and rekey |
| Cross-session chunk confusion | session/direction/message bound AEAD and scoped reassembly quota | reject before allocation; bounded cleanup |
| Relay/SFU inspection | no endpoint keys; encrypted payloads/media | ciphertext-only integration capture |
| Worker privilege escalation | Hub-issued task, capability, lease and consent snapshot | attempt fencing; artifact quarantine |
| Worker-to-worker orchestration | no queue/scheduler dependency or credentials | static architecture gate |
| Evidence poisoning/collusion | bilateral offer, provenance, quarantine and admission policy | contributor-aware evaluation and revocation |
| Dataset/model deletion gap | immutable lineage plus transitive impact graph | revoke, fence publication, rebuild or disable adapter |
| Content leakage in telemetry | field allowlists, bounded public reason codes, epoch digest | secret-injection log/trace/audit/metric tests |

## Mode boundaries

Strict E2EE permits plaintext only at participating browsers. Normal encrypted media permits normal WebRTC endpoint processing but gives relay/SFU no endpoint keys. Consented worker mode is an explicit, independently revocable purpose and is never implied by joining a Pair session, enabling Voice or accepting ordinary media.

Security checks are repeated at request admission, dispatch, worker input release and artifact publication. Late results are rejected after permission, membership, consent, contract, key epoch, attempt or lease changes. Failures fall back only to the already healthy ordinary path; they never downgrade confidentiality silently.
