"""DRR-T026/T027: Repair execution record persistence tests.
DRR-T030: Full lifecycle integration test.
"""
from __future__ import annotations

from sqlmodel import SQLModel, Session, create_engine

from agent.db_models import RepairExecutionRecordDB
from agent.services.repair_outcome_service import persist_repair_execution_result
from worker.core.execution_envelope import (
    RepairExecutionResult,
    RepairResultVerdict,
    RepairStepResult,
    RepairStepResultStatus,
)


def _make_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


class TestRepairExecutionRecordRepository:
    def test_save_and_get_by_id(self):
        engine = _make_engine()
        entry = RepairExecutionRecordDB(
            plan_id="plan-test-001",
            procedure_id="proc-test-v1",
            problem_class="package_install_failure",
            execution_status="success",
            outcome_label="succeeded",
            task_id="task-test-001",
        )
        with Session(engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            saved_id = entry.id
        assert saved_id is not None

        with Session(engine) as session:
            loaded = session.get(RepairExecutionRecordDB, saved_id)
        assert loaded is not None
        assert loaded.procedure_id == "proc-test-v1"

    def test_query_by_problem_class(self):
        engine = _make_engine()
        def _save(**kw):
            with Session(engine) as s:
                e = RepairExecutionRecordDB(**kw)
                s.add(e)
                s.commit()
        _save(plan_id="p1", procedure_id="proc1", problem_class="port_conflict",
              execution_status="success", outcome_label="succeeded", task_id="t1")
        _save(plan_id="p2", procedure_id="proc2", problem_class="port_conflict",
              execution_status="failed", outcome_label="failed", task_id="t2")
        _save(plan_id="p3", procedure_id="proc3", problem_class="service_start_failure",
              execution_status="success", outcome_label="succeeded", task_id="t3")

        with Session(engine) as s:
            from sqlmodel import select
            results = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.problem_class == "port_conflict")
            ).all()
        assert len(results) == 2

        with Session(engine) as s:
            results = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.problem_class == "nonexistent")
            ).all()
        assert len(results) == 0

    def test_query_by_signature_id(self):
        engine = _make_engine()
        with Session(engine) as s:
            e = RepairExecutionRecordDB(
                plan_id="p1", procedure_id="proc1", problem_class="pc1",
                signature_id="sig-001", execution_status="success",
                outcome_label="succeeded", task_id="t1",
            )
            s.add(e)
            s.commit()
        with Session(engine) as s:
            from sqlmodel import select
            results = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.signature_id == "sig-001")
            ).all()
        assert len(results) == 1

    def test_query_by_procedure_id(self):
        engine = _make_engine()
        with Session(engine) as s:
            e = RepairExecutionRecordDB(
                plan_id="p1", procedure_id="proc-unique",
                problem_class="pc1", execution_status="success",
                outcome_label="succeeded", task_id="t1",
            )
            s.add(e)
            s.commit()
        with Session(engine) as s:
            from sqlmodel import select
            results = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.procedure_id == "proc-unique")
            ).all()
        assert len(results) == 1

    def test_recent_by_environment(self):
        engine = _make_engine()
        def _save(**kw):
            with Session(engine) as s:
                e = RepairExecutionRecordDB(**kw)
                s.add(e)
                s.commit()
        _save(plan_id="p1", procedure_id="proc1", problem_class="pc1",
              environment_facts_hash="env-abc", execution_status="success",
              outcome_label="succeeded", task_id="t1")
        _save(plan_id="p2", procedure_id="proc2", problem_class="pc2",
              environment_facts_hash="env-abc", execution_status="failed",
              outcome_label="failed", task_id="t2")
        with Session(engine) as s:
            from sqlmodel import select
            results = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.environment_facts_hash == "env-abc")
            ).all()
        assert len(results) == 2

    def test_regression_flag(self):
        engine = _make_engine()
        with Session(engine) as s:
            e = RepairExecutionRecordDB(
                plan_id="p1", procedure_id="proc1", problem_class="pc1",
                execution_status="success", outcome_label="regressed",
                regression_flag=True, task_id="t1",
            )
            s.add(e)
            s.commit()
            s.refresh(e)
            assert e.regression_flag is True


class TestRepairPersistenceIntegration:
    """DRR-T030: Full lifecycle integration test."""

    def _make_repair_result(
        self,
        *,
        status: RepairResultVerdict = RepairResultVerdict.success,
        plan_id: str = "plan-lifecycle-001",
    ) -> RepairExecutionResult:
        return RepairExecutionResult(
            plan_id=plan_id,
            procedure_id="proc-lifecycle-v1",
            status=status,
            completed_steps=["s1", "s2"],
            skipped_steps=[],
            step_results=[
                RepairStepResult(
                    step_id="s1",
                    status=RepairStepResultStatus.success,
                    reason_code="executed",
                    artifacts=[{"artifact_id": "art-s1", "kind": "inspection"}],
                ),
                RepairStepResult(
                    step_id="s2",
                    status=RepairStepResultStatus.success,
                    reason_code="executed",
                    artifacts=[{"artifact_id": "art-s2", "kind": "fix"}],
                ),
            ],
            outcome_label="succeeded",
            artifacts=[
                {"artifact_id": "art-plan", "kind": "repair_execution_result"},
            ],
        )

    def _patch_engine(self, monkeypatch) -> None:
        engine = _make_engine()
        import agent.database as db_mod
        import agent.repositories.repair_execution_record as repo_mod
        monkeypatch.setattr(db_mod, "engine", engine)
        monkeypatch.setattr(repo_mod, "engine", engine)
        return engine

    def test_persist_and_query_full_lifecycle(self, monkeypatch):
        engine = self._patch_engine(monkeypatch)

        result = self._make_repair_result()
        outcome = persist_repair_execution_result(
            result,
            goal_id="goal-001",
            task_id="task-001",
            worker_job_id="wjob-001",
            platform_target="linux-x86_64",
            signature_id="sig-lifecycle-001",
            environment_facts_hash="env-hash-001",
        )
        assert outcome["persisted"] is True
        assert outcome["id"] is not None

        with Session(engine) as s:
            from sqlmodel import select
            records = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.plan_id == "plan-lifecycle-001")
            ).all()
            assert len(records) == 1
            record = records[0]
            assert record.procedure_id == "proc-lifecycle-v1"
            assert record.execution_status == "success"
            assert record.outcome_label == "succeeded"
            assert record.signature_id == "sig-lifecycle-001"
            assert record.goal_id == "goal-001"
            assert record.task_id == "task-001"

    def test_persist_with_denied_status(self, monkeypatch):
        engine = self._patch_engine(monkeypatch)

        result = self._make_repair_result(
            status=RepairResultVerdict.denied,
            plan_id="plan-denied-001",
        )
        result.completed_steps = []
        result.step_results = []
        outcome = persist_repair_execution_result(result, task_id="task-denied-001")
        assert outcome["persisted"] is True

        with Session(engine) as s:
            from sqlmodel import select
            records = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.plan_id == "plan-denied-001")
            ).all()
            assert len(records) == 1
            assert records[0].execution_status == "denied"

    def test_persist_error_returns_not_persisted(self, monkeypatch):
        engine = self._patch_engine(monkeypatch)

        result = self._make_repair_result()
        result.plan_id = None
        try:
            outcome = persist_repair_execution_result(result)
            assert outcome["persisted"] is False
        except Exception:
            pass

    def test_persist_with_extra_metadata(self, monkeypatch):
        """DRR-T033: extra_metadata stores audit trail."""
        engine = self._patch_engine(monkeypatch)

        result = RepairExecutionResult(
            plan_id="plan-meta-001",
            procedure_id="proc-meta-v1",
            status=RepairResultVerdict.success,
            completed_steps=["s1"],
            failed_step_id=None,
            approval_required_step_id=None,
            step_results=[
                RepairStepResult(
                    step_id="s1",
                    status=RepairStepResultStatus.success,
                    reason_code="executed",
                ),
            ],
        )
        outcome = persist_repair_execution_result(result, task_id="task-meta-001")
        assert outcome["persisted"] is True

        with Session(engine) as s:
            from sqlmodel import select
            records = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.plan_id == "plan-meta-001")
            ).all()
            assert len(records) == 1
            meta = records[0].extra_metadata
            assert meta["completed_steps"] == ["s1"]
            assert meta["failed_step_id"] is None
            assert meta["approval_required_step_id"] is None

    def test_persist_artifact_refs_and_trace(self, monkeypatch):
        """DRR-T031/T032: Artifact refs and trace ref are persisted."""
        engine = self._patch_engine(monkeypatch)

        result = RepairExecutionResult(
            plan_id="plan-art-001",
            procedure_id="proc-art-v1",
            status=RepairResultVerdict.success,
            completed_steps=["s1"],
            step_results=[
                RepairStepResult(
                    step_id="s1",
                    status=RepairStepResultStatus.success,
                    reason_code="executed",
                ),
            ],
            artifacts=[
                {"artifact_id": "art-1", "kind": "repair_step"},
                {"artifact_id": "art-2", "kind": "verification"},
            ],
            trace_bundle_ref="trace:repair-001",
        )
        outcome = persist_repair_execution_result(result, task_id="task-art-001")
        assert outcome["persisted"] is True

        with Session(engine) as s:
            from sqlmodel import select
            records = s.exec(
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.plan_id == "plan-art-001")
            ).all()
            assert len(records) == 1
            record = records[0]
            assert "art-1" in record.artifact_refs
            assert "art-2" in record.artifact_refs
            assert record.trace_ref == "trace:repair-001"

    def test_persist_regression_flag(self, monkeypatch):
        """DRR-T034: Regression flag and outcome tracking."""
        engine = self._patch_engine(monkeypatch)

        for label, expected_flag in [("succeeded", False), ("regressed", True), ("failed", False)]:
            result = RepairExecutionResult(
                plan_id=f"plan-reg-{label}",
                procedure_id="proc-reg-v1",
                status=RepairResultVerdict.success,
                completed_steps=["s1"],
                step_results=[
                    RepairStepResult(
                        step_id="s1",
                        status=RepairStepResultStatus.success,
                        reason_code="executed",
                    ),
                ],
                outcome_label=label,
            )
            outcome = persist_repair_execution_result(result, task_id=f"task-reg-{label}")
            assert outcome["persisted"] is True

            with Session(engine) as s:
                from sqlmodel import select
                records = s.exec(
                    select(RepairExecutionRecordDB)
                    .where(RepairExecutionRecordDB.plan_id == f"plan-reg-{label}")
                ).all()
                assert len(records) == 1
                assert records[0].regression_flag == expected_flag
                assert records[0].outcome_label == label
