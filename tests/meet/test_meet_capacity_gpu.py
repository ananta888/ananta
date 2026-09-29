"""Real private RTX worker, two simultaneous Hub requests, synthetic scopes."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select

from agent.repositories.meet_capacity import SqlMeetCapacity, slots
from agent.services.meet_capacity_admission import MeetCapacityAdmission
from agent.services.meet_media_transport import HttpMediaWorker
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from ananta_contracts.meet_speech import speech_profile


@pytest.mark.skipif(os.environ.get("MEET_MEDIA_GPU_GATE") != "1", reason="opt-in private GPU worker")
@pytest.mark.timeout(150)
def test_two_real_gpu_requests_are_serialized_by_hub_capacity(app, tmp_path, monkeypatch):
    from sqlalchemy import event
    from sqlmodel import Session, SQLModel

    from agent.database import configure_sqlite_connection
    from agent.db_models import ProjectDB, ProjectMembershipDB
    from worker.meet_media.contract import load_key

    # Shared-cache in-memory SQLite has table-lock behavior unlike the deployed
    # file-backed database. Keep actual concurrent Task writes and use a private
    # file-backed Hub database with FK enforcement, not serialized test doubles.
    engine = create_engine(f"sqlite:///{tmp_path / 'gpu-hub.sqlite'}", connect_args={"timeout": 3})

    @event.listens_for(engine, "connect")
    def constraints(connection, _record):
        configure_sqlite_connection(connection)

    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("agent.database.engine", engine)
    # Each request thread opens its own FK-enforcing connection. Provision the
    # synthetic parent and members before either normal Hub task is ingested.
    with Session(engine) as database:
        database.merge(
            ProjectDB(
                tenant_id="synthetic-gpu",
                project_id="synthetic-gpu",
                name="Synthetic GPU capacity test",
                created_by_subject_id="synthetic-actor-0",
            )
        )
        database.commit()
        for index in range(2):
            database.merge(
                ProjectMembershipDB(
                    tenant_id="synthetic-gpu",
                    project_id="synthetic-gpu",
                    subject_id=f"synthetic-actor-{index}",
                    role="owner",
                )
            )
        database.commit()
    store = SqlMeetCapacity(engine, "synthetic-gpu-capacity")
    store.initialize()
    tasks = HubMediaTasks()
    barrier, lock = threading.Barrier(2), threading.Lock()
    active = maximum = 0
    intervals = []

    class ObservedWorker:
        def __init__(self):
            self.worker = HttpMediaWorker(
                os.environ["MEET_MEDIA_GPU_ENDPOINT"],
                load_key(os.environ["MEET_MEDIA_GPU_KEY_FILE"]),
            )

        def execute(self, turn):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            started = time.monotonic()
            try:
                result = self.worker.execute(turn)
                assert result["engines"]["speech"] == "piper-cuda"
                return result
            finally:
                with lock:
                    intervals.append((started, time.monotonic()))
                    active -= 1

    def execute(index):
        # Separate service/store instances model competing Hub request owners;
        # the actual Hub Task DB is shared, with no parallel worker scheduler.
        capacity = MeetCapacityAdmission(SqlMeetCapacity(engine, store.pool), tasks)
        runtime = MeetTurnService(
            Mock(),
            ObservedWorker(),
            tasks,
            [("synthetic-gpu", "synthetic-gpu")],
            speech_profile=speech_profile(max_seconds=10),
            capacity=capacity,
        )
        principal = SimpleNamespace(tenant_id="synthetic-gpu", subject_id=f"synthetic-actor-{index}")
        with app.app_context():
            barrier.wait(timeout=10)
            return runtime.execute(principal, "synthetic-gpu", {"text": "Sage nur: Hallo."})

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(execute, range(2)))
        assert len({result["task_id"] for result in results}) == 2
        assert maximum == 1 and active == 0
        intervals.sort()
        assert intervals[0][1] <= intervals[1][0]
        with engine.connect() as connection:
            assert list(connection.execute(select(slots.c.status)).scalars()) == ["released", "released"]
    finally:
        engine.dispose()
