# Hub Evidence Registry Blocker Audit

Status: 2026-09-02

## Purpose

Missing `SRC_*` or `RUN_*` strings are no longer automatically classified as
external input. The Hub can now admit immutable sources and reserve bound runs
without human interaction. This audit separates locally actionable registry
integration from prerequisites that still require real accounts, datasets,
hardware/runtime support, legal facts or external infrastructure.

## Completed local registry integrations

| Track | Result | Stable evidence |
|---|---|---|
| Agent defense in depth | `ADS-047` complete; 21 automatic chaos/safety tests passed without skips. The completed track is archived. | `artifacts/domain/agent-safety-hub-evidence.json` |
| JMAP default provider | `T00` and every repository-local gate complete; 148 warnings-strict tests passed without skips. Only the real provider smoke remains external. | `artifacts/domain/jmap-source-baseline.json` |
| LM Studio/Ollama runtime | `LMR-000` complete; 99 warnings-strict local-runtime tests passed without skips. The locally scoped track is archived; live runtime claims remain separate. | `artifacts/domain/local-model-runtime-source-baseline.json` |
| Qdrant vector store | `QVS-019` and dependent Wiki gate `QVS-021` complete; all six real TLS scenarios passed without skips and cleanup completed. | `artifacts/domain/qdrant-hub-evidence.json` |

All four gates now use the common Hub-owned coordinator and fixed-profile
pytest runner. Each source is admitted and each run reserved before execution;
the Worker receives only a closed assignment projection and the Hub verifies
the terminal result under the same binding. No old log was relabeled as new
evidence.

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
- `agent.services.hub_evidence_gate_service` coordinates fixed-profile gate
  execution without moving orchestration into a Worker.
- Organization Source Catalog and Recovery run evidence remain specialized
  Hub adapters during additive migration; neither makes Workers an issuer.
- Test/synthetic evidence may exercise every branch automatically but cannot
  satisfy production release verification.
- IDs establish identity and binding, not truth by themselves. Missing
  datasets, credentials, licenses, hardware measurements or runtime behavior
  remain genuine blockers.
