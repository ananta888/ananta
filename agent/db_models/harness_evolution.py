"""Append-only persistence for Harness Genome evolution (SEH-004).

Content-addressed genomes, mutation edges, experiments and benchmark runs. Every
row carries a canonical content hash; rows are immutable, so lineage can be
reconstructed and every benchmark value is traceable to genome, code commit,
model, dataset version and runtime.
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlmodel import JSON, Column, Field, SQLModel


def _uuid() -> str:
    return uuid.uuid4().hex


class HarnessGenomeDB(SQLModel, table=True):
    __tablename__ = "harness_genomes"
    genome_id: str = Field(primary_key=True)
    schema_version: str
    canonical_json: str
    content_hash: str = Field(index=True)
    code_hash: str = Field(index=True)
    config_hash: str
    parents: List[str] = Field(default=[], sa_column=Column(JSON))
    mutation_operators: List[str] = Field(default=[], sa_column=Column(JSON))
    model_requirements: dict = Field(default={}, sa_column=Column(JSON))
    runtime_requirements: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class HarnessMutationDB(SQLModel, table=True):
    __tablename__ = "harness_mutations"
    mutation_id: str = Field(default_factory=_uuid, primary_key=True)
    parent_genome_id: str = Field(index=True)
    child_genome_id: str = Field(index=True)
    operator: str = Field(index=True)
    hypothesis: str = ""
    content_hash: str = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)


class HarnessExperimentDB(SQLModel, table=True):
    __tablename__ = "harness_experiments"
    experiment_id: str = Field(default_factory=_uuid, primary_key=True)
    genome_id: str = Field(index=True)
    baseline_genome_id: Optional[str] = Field(default=None, index=True)
    task: str = Field(index=True)
    model: str = Field(index=True)
    dataset_version: str = Field(index=True)
    code_commit: str
    runtime_id: str
    pareto_status: str = Field(default="unranked", index=True)
    status: str = Field(default="created", index=True)
    content_hash: str = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)


class HarnessBenchmarkRunDB(SQLModel, table=True):
    __tablename__ = "harness_benchmark_runs"
    run_id: str = Field(default_factory=_uuid, primary_key=True)
    experiment_id: str = Field(index=True)
    genome_id: str = Field(index=True)
    metrics: dict = Field(default={}, sa_column=Column(JSON))
    gate_results: List[dict] = Field(default=[], sa_column=Column(JSON))
    artifact_refs: List[str] = Field(default=[], sa_column=Column(JSON))
    trace_ref: Optional[str] = None
    content_hash: str = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)
