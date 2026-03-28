from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import ContextBundleDB, RetrievalRunDB, WorkerJobDB, WorkerResultDB


class RetrievalRunRepository:
    def get_by_id(self, run_id: str):
        with Session(engine) as session:
            return session.get(RetrievalRunDB, run_id)

    def save(self, run: RetrievalRunDB):
        with Session(engine) as session:
            session.add(run)
            session.commit()
            session.refresh(run)
            return run


class ContextBundleRepository:
    def get_by_id(self, bundle_id: str):
        with Session(engine) as session:
            return session.get(ContextBundleDB, bundle_id)

    def get_by_task(self, task_id: str) -> List[ContextBundleDB]:
        with Session(engine) as session:
            statement = select(ContextBundleDB).where(ContextBundleDB.task_id == task_id).order_by(ContextBundleDB.created_at.desc())
            return session.exec(statement).all()

    def save(self, bundle: ContextBundleDB):
        with Session(engine) as session:
            session.add(bundle)
            session.commit()
            session.refresh(bundle)
            return bundle


class WorkerJobRepository:
    def get_by_id(self, job_id: str):
        with Session(engine) as session:
            return session.get(WorkerJobDB, job_id)

    def get_by_parent_task(self, parent_task_id: str) -> List[WorkerJobDB]:
        with Session(engine) as session:
            statement = select(WorkerJobDB).where(WorkerJobDB.parent_task_id == parent_task_id).order_by(WorkerJobDB.created_at.desc())
            return session.exec(statement).all()

    def save(self, job: WorkerJobDB):
        with Session(engine) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
            return job


class WorkerResultRepository:
    def get_by_worker_job(self, worker_job_id: str) -> List[WorkerResultDB]:
        with Session(engine) as session:
            statement = select(WorkerResultDB).where(WorkerResultDB.worker_job_id == worker_job_id).order_by(WorkerResultDB.created_at.desc())
            return session.exec(statement).all()

    def save(self, result: WorkerResultDB):
        with Session(engine) as session:
            session.add(result)
            session.commit()
            session.refresh(result)
            return result
