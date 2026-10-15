"""Append-only Harness Genome / experiment / lineage store (SEH-004)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence

from sqlmodel import Session, select

from agent.db_models import (
    HarnessBenchmarkRunDB,
    HarnessExperimentDB,
    HarnessGenomeDB,
    HarnessMutationDB,
)
from ananta_contracts.harness_genome import HarnessGenome, canonical_json, content_digest

MAX_LINEAGE_DEPTH = 128


def _engine() -> object:
    from agent.database import engine

    return engine


class HarnessEvolutionStore:
    """Content-addressed, append-only persistence for harness evolution."""

    def __init__(self, *, engine: Callable[[], object] | None = None, clock: Callable[[], float] = time.time):
        self._engine = engine or _engine
        self._clock = clock

    # ── genomes ──────────────────────────────────────────────────────────────
    def append_genome(self, genome: HarnessGenome, *, model_requirements: Mapping | None = None,
                      runtime_requirements: Mapping | None = None) -> str:
        genome.assert_valid()
        canonical = genome.to_json()
        content_hash = content_digest(genome.semantic_mapping())
        # Compare the JSON representation that survives database round trips.
        model_requirements = json.loads(canonical_json(model_requirements)) if model_requirements is not None else None
        runtime_requirements = (
            json.loads(canonical_json(runtime_requirements)) if runtime_requirements is not None else None
        )
        with Session(self._engine()) as session:
            existing = session.get(HarnessGenomeDB, genome.genome_id)
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise ValueError("harness_genome_content_conflict")
                if (
                    model_requirements is not None and existing.model_requirements != dict(model_requirements)
                    or runtime_requirements is not None and existing.runtime_requirements != dict(runtime_requirements)
                ):
                    raise ValueError("harness_genome_requirements_immutable")
                return genome.genome_id
            session.add(HarnessGenomeDB(
                genome_id=genome.genome_id, schema_version=genome.schema, canonical_json=canonical,
                content_hash=content_hash, code_hash=genome.code_hash, config_hash=genome.config_hash,
                parents=list(genome.parents), mutation_operators=list(genome.mutation_operators),
                model_requirements=dict(model_requirements or {}),
                runtime_requirements=dict(runtime_requirements or {}), created_at=self._clock(),
            ))
            session.commit()
            return genome.genome_id

    def get_genome(self, genome_id: str) -> dict | None:
        with Session(self._engine()) as session:
            row = session.get(HarnessGenomeDB, genome_id)
            return self._genome_dict(row) if row is not None else None

    @staticmethod
    def _genome_dict(row: HarnessGenomeDB) -> dict:
        return {"genome_id": row.genome_id, "schema": row.schema_version, "canonical_json": row.canonical_json,
                "content_hash": row.content_hash, "code_hash": row.code_hash, "config_hash": row.config_hash,
                "parents": list(row.parents), "mutation_operators": list(row.mutation_operators),
                "model_requirements": dict(row.model_requirements),
                "runtime_requirements": dict(row.runtime_requirements), "created_at": row.created_at}

    # ── mutations / lineage ──────────────────────────────────────────────────
    def append_mutation(self, *, parent_genome_id: str, child_genome_id: str, operator: str,
                        hypothesis: str = "") -> str:
        if not operator or len(operator) > 200:
            raise ValueError("harness_mutation_operator_invalid")
        content_hash = content_digest({"parent": parent_genome_id, "child": child_genome_id,
                                       "operator": operator, "hypothesis": hypothesis})
        with Session(self._engine()) as session:
            existing = session.exec(select(HarnessMutationDB).where(
                HarnessMutationDB.content_hash == content_hash)).first()
            if existing is not None:
                return existing.mutation_id
            row = HarnessMutationDB(parent_genome_id=parent_genome_id, child_genome_id=child_genome_id,
                                    operator=operator, hypothesis=hypothesis, content_hash=content_hash,
                                    created_at=self._clock())
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.mutation_id

    def lineage(self, genome_id: str, *, max_depth: int = MAX_LINEAGE_DEPTH) -> list[dict]:
        """Full ancestor chain from ``genome_id`` back to the roots (newest first)."""
        if type(max_depth) is not int or not 1 <= max_depth <= MAX_LINEAGE_DEPTH:
            raise ValueError("harness_lineage_depth_invalid")
        chain: list[dict] = []
        seen: set[str] = set()
        frontier = [genome_id]
        with Session(self._engine()) as session:
            while frontier:
                current = frontier.pop(0)
                if current in seen or len(chain) >= max_depth:
                    continue
                seen.add(current)
                row = session.get(HarnessGenomeDB, current)
                if row is None:
                    raise ValueError("harness_genome_unknown")
                chain.append(self._genome_dict(row))
                parents = [edge.parent_genome_id for edge in session.exec(
                    select(HarnessMutationDB).where(HarnessMutationDB.child_genome_id == current)).all()]
                frontier.extend(parents or list(row.parents))
        return chain

    # ── experiments ──────────────────────────────────────────────────────────
    def start_experiment(self, *, genome_id: str, task: str, model: str, dataset_version: str,
                         code_commit: str, runtime_id: str, baseline_genome_id: str | None = None) -> str:
        for value, reason in ((task, "harness_experiment_task_invalid"), (model, "harness_experiment_model_invalid"),
                              (dataset_version, "harness_experiment_dataset_invalid"),
                              (code_commit, "harness_experiment_commit_invalid"),
                              (runtime_id, "harness_experiment_runtime_invalid")):
            if not isinstance(value, str) or not value or len(value) > 400:
                raise ValueError(reason)
        with Session(self._engine()) as session:
            if session.get(HarnessGenomeDB, genome_id) is None:
                raise ValueError("harness_genome_unknown")
            content_hash = content_digest({"genome_id": genome_id, "baseline": baseline_genome_id, "task": task,
                                           "model": model, "dataset_version": dataset_version,
                                           "code_commit": code_commit, "runtime_id": runtime_id})
            row = HarnessExperimentDB(genome_id=genome_id, baseline_genome_id=baseline_genome_id, task=task,
                                      model=model, dataset_version=dataset_version, code_commit=code_commit,
                                      runtime_id=runtime_id, content_hash=content_hash, created_at=self._clock())
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.experiment_id

    def record_benchmark_run(self, *, experiment_id: str, metrics: Mapping, gate_results: Sequence[Mapping] = (),
                             artifact_refs: Sequence[str] = (), trace_ref: str | None = None) -> str:
        if not isinstance(metrics, Mapping) or not metrics:
            raise ValueError("harness_metrics_invalid")
        for metric, value in metrics.items():
            if (not isinstance(metric, str) or not metric or isinstance(value, bool)
                    or not isinstance(value, (int, float))):
                raise ValueError("harness_metrics_invalid")
        gates = [dict(item) for item in gate_results]
        for gate in gates:
            if not isinstance(gate.get("gate_ref"), str) or not gate["gate_ref"]:
                raise ValueError("harness_gate_result_invalid")
            if not isinstance(gate.get("passed"), bool):
                raise ValueError("harness_gate_result_invalid")
        with Session(self._engine()) as session:
            experiment = session.get(HarnessExperimentDB, experiment_id)
            if experiment is None:
                raise ValueError("harness_experiment_unknown")
            content_hash = content_digest({"experiment_id": experiment_id, "metrics": dict(metrics),
                                           "gate_results": gates, "artifact_refs": list(artifact_refs),
                                           "trace_ref": trace_ref})
            row = HarnessBenchmarkRunDB(experiment_id=experiment_id, genome_id=experiment.genome_id,
                                        metrics=dict(metrics), gate_results=gates,
                                        artifact_refs=list(artifact_refs), trace_ref=trace_ref,
                                        content_hash=content_hash, created_at=self._clock())
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.run_id

    def set_experiment_state(self, experiment_id: str, *, status: str | None = None,
                             pareto_status: str | None = None) -> None:
        allowed_status = {"created", "running", "succeeded", "failed", "rejected", "promoted"}
        allowed_pareto = {"unranked", "front", "dominated", "rejected"}
        if status is not None and status not in allowed_status:
            raise ValueError("harness_experiment_status_invalid")
        if pareto_status is not None and pareto_status not in allowed_pareto:
            raise ValueError("harness_pareto_status_invalid")
        with Session(self._engine()) as session:
            row = session.get(HarnessExperimentDB, experiment_id)
            if row is None:
                raise ValueError("harness_experiment_unknown")
            if status is not None:
                row.status = status
            if pareto_status is not None:
                row.pareto_status = pareto_status
            session.add(row)
            session.commit()

    def experiments(self, *, model: str | None = None, task: str | None = None, genome_id: str | None = None,
                    mutation_operator: str | None = None, gate: str | None = None,
                    pareto_status: str | None = None, limit: int = 200) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("harness_query_limit_invalid")
        with Session(self._engine()) as session:
            statement = select(HarnessExperimentDB)
            if model is not None:
                statement = statement.where(HarnessExperimentDB.model == model)
            if task is not None:
                statement = statement.where(HarnessExperimentDB.task == task)
            if genome_id is not None:
                statement = statement.where(HarnessExperimentDB.genome_id == genome_id)
            if pareto_status is not None:
                statement = statement.where(HarnessExperimentDB.pareto_status == pareto_status)
            rows = list(session.exec(statement.order_by(HarnessExperimentDB.created_at)).all())
            if mutation_operator is not None:
                child_ids = {edge.child_genome_id for edge in session.exec(
                    select(HarnessMutationDB).where(HarnessMutationDB.operator == mutation_operator)).all()}
                rows = [row for row in rows if row.genome_id in child_ids]
            if gate is not None:
                run_rows = session.exec(select(HarnessBenchmarkRunDB)).all()
                experiment_ids = {run.experiment_id for run in run_rows
                                  if any(g.get("gate_ref") == gate for g in run.gate_results)}
                rows = [row for row in rows if row.experiment_id in experiment_ids]
            return [self._experiment_dict(row) for row in rows[:limit]]

    def benchmark_runs(self, experiment_id: str, *, limit: int = 200) -> list[dict]:
        with Session(self._engine()) as session:
            rows = session.exec(select(HarnessBenchmarkRunDB).where(
                HarnessBenchmarkRunDB.experiment_id == experiment_id)
                .order_by(HarnessBenchmarkRunDB.created_at).limit(limit)).all()
            return [self._run_dict(row) for row in rows]

    @staticmethod
    def _experiment_dict(row: HarnessExperimentDB) -> dict:
        return {"experiment_id": row.experiment_id, "genome_id": row.genome_id,
                "baseline_genome_id": row.baseline_genome_id, "task": row.task, "model": row.model,
                "dataset_version": row.dataset_version, "code_commit": row.code_commit,
                "runtime_id": row.runtime_id, "pareto_status": row.pareto_status, "status": row.status,
                "content_hash": row.content_hash, "created_at": row.created_at}

    @staticmethod
    def _run_dict(row: HarnessBenchmarkRunDB) -> dict:
        return {"run_id": row.run_id, "experiment_id": row.experiment_id, "genome_id": row.genome_id,
                "metrics": dict(row.metrics), "gate_results": list(row.gate_results),
                "artifact_refs": list(row.artifact_refs), "trace_ref": row.trace_ref,
                "content_hash": row.content_hash, "created_at": row.created_at}
