# Hub Evidence Registry Blocker Audit

Status: 2026-09-03

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
| DSPy optimization baseline | `DSPY-001` complete; immutable DSPy 3.2.1 artifacts, licenses, 67-package hash lock and the DiskCache mitigation are bound; 33 automatic tests passed without skips. Production promotion remains separate. | `artifacts/domain/dspy-local-evidence.json` |

All five gates now use the common Hub-owned coordinator and fixed-profile
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
| Dendritic memory workbench | Local lifecycle mechanics are tested, but no admitted real three-seed/two-task-family benchmark or practical staging rollback/revoke/delete run exists. |
| Multi-training backends | Current image scans contain unresolved vulnerabilities/licenses; approved models, datasets and NVIDIA runs are absent. |
| Qdrant performance profile | A complete reference-host run with sufficient resources is absent. Registry issuance alone cannot turn an inconclusive measurement into passed. |
| Unsloth platform | Approved datasets/models/images, transitive license clearance, Studio endpoint and containerized NVIDIA execution are absent. |
| WebRTC SFU broadcast | Pinned real SFU/browser/TURN runtime, public network, multi-host scale and physical Safari/mobile evidence are absent. |

## Reclassification results

- `DSPY-001` was an identity-only blocker and is now complete. The remaining
  `DSPY-040` blocker names the real production dataset/provider, image/SBOM,
  quality, cost, recovery and rollback evidence that is absent.
- `NCH-030` was locally actionable, not externally blocked. It is now complete
  with five machine-evaluated rollout phases, default kill switch, automatic
  progression, isolated rollback and review-only upstream watch.
- `DEND-071` now consumes exact Hub Evidence Registry bindings. It remains
  blocked because two real experiment facts are missing, not because IDs must
  be typed in: a three-seed/two-task-family model/dataset comparison and a
  practical staging lifecycle run. The optional real-model tests correctly
  skip when no immutable Safetensors snapshot is available.
- The five untouched Buzz adapter tasks were mislabeled `blocked`. They are now
  ordinary `todo` implementation work behind the partial generic bridge
  contract; real Buzz conformance evidence becomes relevant only after that
  code exists.
- `FCA-005` now records the actual blocker: this host has neither the official
  `qwen` binary nor a supported authenticated provider environment. The
  adapter and fully automatic live-gate already exist.
- `LMM-QA-004` now records the actual blocker: neither LM Studio nor Ollama is
  executable on this host. The Hub can issue evidence automatically after a
  real admitted endpoint and model exist.

After these corrections, 89 task records in 13 normal track files remain
blocked. In addition, one GitHub-CI item needs a real release tag and eight
RTX/Recovery/Bitcoin items remain explicitly deferred by user priority. All 89
normal blocked tasks now have a concrete `blocked_reason`; none cites a missing
identifier as its only prerequisite.

## Current host limits

Read-only probes on 2026-09-03 found 20 logical CPUs, about 61.3 GiB RAM and an
RTX 3080 with 10 GiB VRAM. Docker is present, but no NVIDIA CDI device is
configured for containers. `qwen`, `lms` and `ollama` are absent. Consequently:

- Qdrant's small and medium profiles fit, while its required 128-GiB large
  reference profile cannot run on this host.
- Native GPU visibility does not establish containerized Unsloth,
  multi-training-backend or Dendritic release evidence.
- Provider live-gates cannot manufacture authenticated accounts, model
  licenses, datasets, public TURN/DNS/TLS infrastructure or physical
  Safari/mobile devices.

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
