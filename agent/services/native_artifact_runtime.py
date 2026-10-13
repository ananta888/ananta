"""Production transaction/authority composition for authenticated Native ingress."""

from sqlmodel import Session

from agent.repositories.sqlalchemy_support import sqlite_transaction_guard
from agent.services.native_artifact_admission import NativeArtifactAdmissionService
from agent.services.native_artifact_dispatch import NativeArtifactDispatch
from agent.services.native_artifact_inputs import NativeArtifactInputService
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority


class NativeArtifactRuntime:
    def __init__(self, *, engine, key_ring, storage, clock=None):
        self._engine, self._key_ring, self._storage, self._clock = engine, key_ring, storage, clock

    def execute(self, *, worker_id, worker_url, task_id, transfer=None):
        authority = SQLAlchemyNativeResultAuthority(key_ring=self._key_ring)
        service = NativeArtifactAdmissionService(
            authority=authority,
            dispatch=NativeArtifactDispatch(authority=authority, worker_id=worker_id, worker_url=worker_url),
            storage=self._storage, **({"clock": self._clock} if self._clock is not None else {}),
        )
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session:
            if transfer is None:
                return service.transfer_binding(session=session, task_id=task_id)
            receipts = service.materialize(session=session, task_id=task_id, transfer=transfer)
            session.commit()
            return {"schema": "ananta.native-artifact-receipts.v1", "artifacts": list(receipts)}

    def read_input(self, *, worker_id, worker_url, task_id, input_name):
        authority = SQLAlchemyNativeResultAuthority(key_ring=self._key_ring)
        service = NativeArtifactInputService(
            authority=authority,
            dispatch=NativeArtifactDispatch(authority=authority, worker_id=worker_id, worker_url=worker_url),
            storage=self._storage, **({"clock": self._clock} if self._clock is not None else {}),
        )
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session:
            return service.read(session=session, task_id=task_id, input_name=input_name)


def get_native_artifact_runtime():
    from agent.config import settings

    if str(settings.role).strip().lower() != "hub":
        raise ValueError("native_artifact_hub_required")
    from agent.database import engine
    from agent.services.artifact_store import ArtifactStore
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring

    return NativeArtifactRuntime(engine=engine, key_ring=get_workflow_authorization_key_ring, storage=ArtifactStore())
