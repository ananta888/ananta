# Hub Evidence Registry Blocker Audit

Status: 2026-09-02

## Purpose

Missing `SRC_*` or `RUN_*` strings are no longer automatically classified as
external input. The Hub can now admit immutable sources and reserve bound runs
without human interaction. This audit separates locally actionable registry
integration from prerequisites that still require real accounts, datasets,
hardware/runtime support, legal facts or external infrastructure.

## Locally actionable after the registry change

| Track | Reclassified work | Remaining automatic work |
|---|---|---|
| Agent defense in depth | `ADS-047` changed from blocked to partial | Admit the concrete chaos inputs, reserve the run before dispatch and verify the production-like result under the same assignment/lease. |
| JMAP default provider | `T00` changed from blocked to in progress | Register the repository baseline and rerun local gates under a pre-reserved Hub run. The real provider smoke remains external. |
| LM Studio/Ollama runtime | `LMR-000` changed from blocked to partial | Connect baseline and runtime gate adapters to automatic source admission and run reservation. Live runtimes/models remain separate prerequisites. |
| Qdrant vector store | `QVS-019` changed from blocked to partial | Reserve the real TLS integration job through the Hub and rerun all six scenarios. The reference-host benchmark remains separate. |

These tasks are not complete merely because the registry exists. Their
subsystem runner must transport the closed assignment projection and close the
same reservation with its result. Post-hoc conversion of old logs remains
forbidden.

## Still blocked by facts other than identity issuance

| Track | Non-identity blocker |
|---|---|
| Local multi-model and Needle training | No admitted training/holdout dataset or candidates; local runtime is currently OOM-failed and container GPU is unavailable. |
| Source Control Center | Private GitHub App/OAuth installation, production receiver/metrics and provider journeys are not present. |
| CodeCompass DMoE | No attributable upstream implementation/checkpoint or compatible attested dynamic expert runtime. |
| CodeCompass SIRA | Real multi-repository/model benchmark inputs and a production-like canary observation window are absent. The Hub can issue IDs once those inputs exist. |
| Free coding CLI providers | Qwen Code binary and supported authenticated live environment are absent. |
| GitHub Actions stabilization | Container release evidence requires an explicitly authorized real release tag and non-superseded dogfood run. |
| JMAP provider release | A real JMAP session, dedicated account and short-lived credentials are absent. |
| Local model runtime release | LM Studio is absent; Ollama models/endpoints are not running. |
| RTX/Recovery/Bitcoin follow-up | Explicitly deprioritized/blocked track; Docker GPU CDI is also unavailable. |
| Dendritic memory workbench | Multi-seed/task-family benchmark, staging rollback/revoke and release criteria remain unexecuted. |
| Multi-training backends | Current image scans contain unresolved vulnerabilities/licenses; approved models, datasets and NVIDIA runs are absent. |
| Qdrant performance profile | A complete reference-host run with sufficient resources is absent. Registry issuance alone cannot turn an inconclusive measurement into passed. |
| Unsloth platform | Approved datasets/models/images, transitive license clearance, Studio endpoint and containerized NVIDIA execution are absent. |
| WebRTC SFU broadcast | Pinned real SFU/browser/TURN runtime, public network, multi-host scale and physical Safari/mobile evidence are absent. |

## Invariants

- `agent.services.hub_evidence_registry_service` is the general Hub issuer.
- `agent.repositories.evidence_identity` persists immutable source admissions
  and pre-execution run reservations.
- `ananta_contracts.hub_evidence` is the closed Worker-facing assignment
  projection.
- Organization Source Catalog and Recovery run evidence remain specialized
  Hub adapters during additive migration; neither makes Workers an issuer.
- Test/synthetic evidence may exercise every branch automatically but cannot
  satisfy production release verification.
- IDs establish identity and binding, not truth by themselves. Missing
  datasets, credentials, licenses, hardware measurements or runtime behavior
  remain genuine blockers.
