"""Provider-neutral sentinel training records and safety evaluation summaries."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping, Protocol

from agent.services.agent_safety_errors import AgentSafetyDenied
from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from ananta_contracts.agent_safety import TriggerClass, canonical_digest, require_token, utc_now


class SafetyTrainingPort(Protocol):
    def submit(self, *, channel: str, dataset_digest: str, records: list[dict[str, Any]]) -> Mapping[str, Any]: ...


class AgentSafetyEvaluationService:
    def __init__(
        self,
        store: AgentSafetyStateStorePort,
        *,
        series_signing_key: bytes | None = None,
        training_adapter: SafetyTrainingPort | None = None,
    ) -> None:
        self._store = store
        self._series_signing_key = bytes(series_signing_key or b"")
        self._training_adapter = training_adapter

    def build_trigger_series(self, *, series_id: str, train_count: int, holdout_count: int) -> dict[str, Any]:
        normalized_id = require_token(series_id, "series_id")
        if len(self._series_signing_key) < 32:
            raise AgentSafetyDenied("agent_safety_trigger_rotation_key_unavailable")
        if not 1 <= int(train_count) <= 10_000 or not 1 <= int(holdout_count) <= 10_000:
            raise ValueError("agent_safety_trigger_series_size_invalid")
        symbols = []
        for split, count in (("train", int(train_count)), ("holdout", int(holdout_count))):
            for index in range(count):
                message = f"{normalized_id}:{split}:{index}".encode()
                symbol = hmac.new(self._series_signing_key, message, hashlib.sha256).hexdigest()
                symbols.append({"split": split, "symbol": symbol})
        payload = {
            "series_id": normalized_id,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "series_commitment": canonical_digest({"series_id": normalized_id, "symbols": symbols}),
            "created_at": utc_now(),
        }
        return self._store.append("trigger_series", normalized_id, payload, expected_revision=0)

    def compile_training_records(self, *, policy_id: str, manifests: list[Mapping[str, Any]]) -> dict[str, Any]:
        if not self._store.get("policy", require_token(policy_id, "policy_id")):
            raise KeyError("agent_safety_policy_not_found")
        records = []
        for manifest in manifests:
            trigger_class = TriggerClass(str(manifest.get("trigger_class")))
            records.append(
                {
                    "trigger_class": trigger_class.value,
                    "symbol": str(manifest.get("trigger_id")),
                    "priority_rule": "invoke_before_next_agent_action",
                    "effect_label_exposed": trigger_class != TriggerClass.OPAQUE_PRIORITY,
                    "split": str(manifest.get("split") or "train"),
                }
            )
        if not {item["split"] for item in records}.issuperset({"train", "holdout"}):
            raise AgentSafetyDenied("agent_safety_training_holdout_required")
        return {
            "policy_id": policy_id,
            "records": records,
            "dataset_digest": canonical_digest({"records": records}),
        }

    def submit_training(
        self,
        *,
        policy_id: str,
        channel: str,
        dataset_digest: str,
        records: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        policy = self._store.get("policy", require_token(policy_id, "policy_id"))
        if not policy:
            raise KeyError("agent_safety_policy_not_found")
        if channel not in {"preventive_boundary", "sentinel_priority"}:
            raise ValueError("agent_safety_training_channel_invalid")
        enabled = bool(policy["preventive_training_enabled"]) if channel == "preventive_boundary" else True
        if not enabled:
            return {
                "state": "skipped_disabled",
                "policy_id": policy_id,
                "channel": channel,
                "human_intervention_required": False,
            }
        if self._training_adapter is None:
            raise AgentSafetyDenied("agent_safety_training_adapter_unavailable")
        normalized_records = [dict(item) for item in records]
        if canonical_digest({"records": normalized_records}) != dataset_digest:
            raise AgentSafetyDenied("agent_safety_training_dataset_digest_mismatch")
        receipt = dict(
            self._training_adapter.submit(
                channel=channel,
                dataset_digest=dataset_digest,
                records=normalized_records,
            )
        )
        training_id = require_token(receipt.get("training_id"), "training_id")
        return self._store.append(
            "training_run",
            training_id,
            {
                "training_id": training_id,
                "policy_id": policy_id,
                "channel": channel,
                "dataset_digest": dataset_digest,
                "provider_receipt": receipt,
                "created_at": utc_now(),
            },
            expected_revision=0,
        )

    def evaluate_trials(self, *, evaluation_id: str, trials: list[Mapping[str, Any]]) -> dict[str, Any]:
        if not trials:
            raise ValueError("agent_safety_trials_required")
        grouped: dict[str, dict[str, Any]] = {}
        for trial in trials:
            profile = require_token(trial.get("profile_id"), "profile_id")
            bucket = grouped.setdefault(
                profile,
                {
                    "trials": 0,
                    "triggered": 0,
                    "contained": 0,
                    "self_reported": 0,
                    "bypass_attempts": 0,
                    "grader_only_successes": 0,
                    "collateral_actions": 0,
                    "trigger_latency_ms_total": 0,
                    "boundary_classes": {},
                },
            )
            bucket["trials"] += 1
            bucket["triggered"] += int(bool(trial.get("triggered")))
            bucket["contained"] += int(bool(trial.get("contained")))
            bucket["self_reported"] += int(bool(trial.get("self_reported")))
            bucket["bypass_attempts"] += int(bool(trial.get("bypass_attempted")))
            bucket["grader_only_successes"] += int(bool(trial.get("grader_only_success")))
            bucket["collateral_actions"] += max(int(trial.get("collateral_actions") or 0), 0)
            bucket["trigger_latency_ms_total"] += max(int(trial.get("trigger_latency_ms") or 0), 0)
            boundary_class = str(trial.get("boundary_class") or "none")
            bucket["boundary_classes"][boundary_class] = bucket["boundary_classes"].get(boundary_class, 0) + 1
        for bucket in grouped.values():
            total = int(bucket["trials"])
            bucket["trigger_compliance_rate"] = round(int(bucket["triggered"]) / total, 4)
            bucket["containment_rate"] = round(int(bucket["contained"]) / total, 4)
            bucket["mean_trigger_latency_ms"] = round(int(bucket.pop("trigger_latency_ms_total")) / total, 2)
        result = {
            "evaluation_id": require_token(evaluation_id, "evaluation_id"),
            "profiles": grouped,
            "trial_count": len(trials),
            "grounded_release_evidence": False,
            "created_at": utc_now(),
        }
        return self._store.append("evaluation", evaluation_id, result, expected_revision=0)


__all__ = ["AgentSafetyEvaluationService", "SafetyTrainingPort"]
