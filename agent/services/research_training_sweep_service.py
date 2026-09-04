"""Hub-owned materialization of independently resumable scaling candidates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_run_service import ResearchTrainingDenied, ResearchTrainingRunService
from ananta_contracts.research_training import canonical_digest, require_digest


class ResearchTrainingSweepService:
    def __init__(
        self,
        *,
        recipes: ResearchTrainingRecipeService,
        runs: ResearchTrainingRunService,
        state_path: str | Path,
    ) -> None:
        self._recipes = recipes
        self._runs = runs
        self._path = Path(state_path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def plan(
        self,
        *,
        recipe_request: Mapping[str, Any],
        depths: Sequence[int],
        spec_template: Mapping[str, Any],
    ) -> dict[str, Any]:
        sweep = self._recipes.sweep(recipe_request, depths)
        candidates: list[dict[str, Any]] = []
        for resolved in sweep["recipes"]:
            recipe = {
                key: value
                for key, value in resolved.items()
                if key not in {"recipe_digest", "resolution_is_deterministic"}
            }
            spec = {**dict(spec_template), "recipe": recipe}
            preflight = self._runs.dry_run(spec=spec)
            candidates.append(
                {
                    "depth": recipe["depth"],
                    "recipe_digest": resolved["recipe_digest"],
                    "spec": spec,
                    "preflight": preflight,
                    "shared_dataset_digest": spec["dataset_manifest_digest"],
                }
            )
        result = {
            "schema": "ananta.research-training-sweep-plan.v1",
            "sweep_digest": sweep["sweep_digest"],
            "candidates": candidates,
            "shared_inputs_deduplicated": True,
            "human_intervention_required": False,
        }
        result["plan_digest"] = canonical_digest(result)
        self._persist("plan", result["plan_digest"], result)
        return result

    def materialize(self, *, plan: Mapping[str, Any], idempotency_prefix: str) -> dict[str, Any]:
        candidates = plan.get("candidates")
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or not candidates:
            raise ValueError("research_sweep_plan_invalid")
        runs: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, Mapping) or not isinstance(candidate.get("spec"), Mapping):
                raise ValueError("research_sweep_candidate_invalid")
            try:
                created = self._runs.create(
                    spec=candidate["spec"],
                    idempotency_key=f"{idempotency_prefix}-{index}",
                )
            except ResearchTrainingDenied as exc:
                runs.append(
                    {
                        "candidate_index": index,
                        "state": "denied",
                        "reason_code": str(exc),
                        "run_id": None,
                    }
                )
            else:
                runs.append(
                    {
                        "candidate_index": index,
                        "state": "created",
                        "reason_code": None,
                        "run_id": created["run_id"],
                    }
                )
        result = {
            "schema": "ananta.research-training-sweep-materialization.v1",
            "plan_digest": str(plan.get("plan_digest") or ""),
            "runs": runs,
            "candidate_failures_are_isolated": True,
            "human_intervention_required": False,
        }
        result["materialization_digest"] = canonical_digest(result)
        self._persist("materialization", result["materialization_digest"], result)
        return result

    def compare(
        self,
        *,
        plan_digest: str,
        candidates: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        plan = self.get(kind="plan", record_digest=require_digest(plan_digest, "plan_digest"))
        expected_count = len(plan["candidates"])
        if (
            len(candidates) != expected_count
            or any(set(item) != {"candidate_index", "run_id", "metrics"} for item in candidates)
            or sorted(int(item["candidate_index"]) for item in candidates) != list(range(expected_count))
            or any(not isinstance(item["metrics"], Mapping) for item in candidates)
        ):
            raise ValueError("research_sweep_comparison_candidates_invalid")
        result = {
            "schema": "ananta.research-training-sweep-comparison.v1",
            "plan_digest": plan_digest,
            "candidates": [dict(item) for item in candidates],
            "human_intervention_required": False,
        }
        result["comparison_digest"] = canonical_digest(result)
        self._persist("comparison", result["comparison_digest"], result)
        return result

    def get(self, *, kind: str, record_digest: str) -> dict[str, Any]:
        if kind not in {"plan", "materialization", "comparison"}:
            raise ValueError("research_sweep_record_kind_invalid")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM research_training_sweeps WHERE kind=? AND record_digest=?",
                (kind, require_digest(record_digest, "sweep_record_digest")),
            ).fetchone()
        if row is None:
            raise KeyError("research_sweep_record_not_found")
        return json.loads(row[0])

    def _persist(self, kind: str, digest: str, payload: Mapping[str, Any]) -> None:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._transaction, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM research_training_sweeps WHERE kind=? AND record_digest=?",
                (kind, digest),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != serialized:
                    raise ValueError("research_sweep_record_replay_conflict")
                return
            connection.execute(
                "INSERT INTO research_training_sweeps(kind,record_digest,payload_json) VALUES(?,?,?)",
                (kind, digest, serialized),
            )

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_training_sweeps("
                "kind TEXT NOT NULL,record_digest TEXT NOT NULL,payload_json TEXT NOT NULL,"
                "PRIMARY KEY(kind,record_digest))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["ResearchTrainingSweepService"]
