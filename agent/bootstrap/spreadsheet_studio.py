"""Hub-only composition for the default-off spreadsheet studio mock slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.adapters.spreadsheet_mock_execution_adapter import (
    DeterministicSpreadsheetMockExecutionAdapter,
)
from agent.adapters.spreadsheet_queue_execution_adapter import (
    QueueBoundSpreadsheetExecutionAdapter,
)
from agent.config import settings
from agent.services.spreadsheet_adapter_admission_service import SpreadsheetAdapterAdmissionService
from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_learning_service import SpreadsheetLearningService
from agent.services.spreadsheet_learning_store import SpreadsheetLearningStore
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStore
from agent.services.spreadsheet_training_admission_service import SpreadsheetTrainingAdmissionService
from agent.services.spreadsheet_validation_reference_store import SpreadsheetValidationReferenceStore
from agent.services.spreadsheet_validator_engine import SpreadsheetValidatorEngine


@dataclass(frozen=True, slots=True)
class SpreadsheetStudioWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_spreadsheet_studio(app: Flask) -> SpreadsheetStudioWiringStatus:
    enabled = _bool(app.config.get("ANANTA_SPREADSHEET_STUDIO_ENABLED", settings.spreadsheet_studio_enabled))
    mode = str(app.config.get("ANANTA_SPREADSHEET_STUDIO_MODE", settings.spreadsheet_studio_mode)).strip()
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = SpreadsheetStudioWiringStatus(False, "spreadsheet_hub_role_required")
    else:
        policy = SpreadsheetPolicy(
            enabled=enabled,
            mode=mode,
            automatic_promotion_enabled=_bool(
                app.config.get(
                    "ANANTA_SPREADSHEET_STUDIO_AUTOMATIC_PROMOTION_ENABLED",
                    settings.spreadsheet_studio_automatic_promotion_enabled,
                )
            ),
        )
        try:
            policy.validate()
        except ValueError:
            status = SpreadsheetStudioWiringStatus(False, "spreadsheet_configuration_invalid")
        else:
            if enabled:
                try:
                    state = Path(
                        str(app.config.get("ANANTA_SPREADSHEET_STUDIO_STATE") or settings.spreadsheet_studio_state)
                    )
                    executor = (
                        DeterministicSpreadsheetMockExecutionAdapter()
                        if mode == "mock"
                        else QueueBoundSpreadsheetExecutionAdapter()
                    )
                    if mode == "worker":
                        from agent.database import engine
                        from agent.repositories.spreadsheet_document_repository import (
                            SqlSpreadsheetDocumentRepository,
                        )
                        from agent.repositories.spreadsheet_learning_repository import (
                            SqlSpreadsheetLearningRepository,
                        )
                        from agent.repositories.spreadsheet_validation_reference_repository import (
                            SqlSpreadsheetValidationReferenceRepository,
                        )

                        document_store = SqlSpreadsheetDocumentRepository(db_engine=engine)
                        learning_store = SqlSpreadsheetLearningRepository(db_engine=engine)
                        validation_references = SqlSpreadsheetValidationReferenceRepository(db_engine=engine)
                    else:
                        document_store = SpreadsheetStore(state)
                        learning_store = SpreadsheetLearningStore(state)
                        validation_references = SpreadsheetValidationReferenceStore(
                            state.with_name(f"{state.stem}-validation-references.sqlite3")
                        )
                    artifact_store = SpreadsheetArtifactStore(state.parent / "spreadsheet-artifacts")
                    saga = SpreadsheetSagaService(
                        document_store,
                        policy=policy,
                        executor=executor,
                        artifact_store=artifact_store,
                        validator_engine=SpreadsheetValidatorEngine(validation_references),
                        validation_references=validation_references,
                        training_available=True,
                    )
                    app.extensions["spreadsheet_studio_service"] = saga
                    if mode == "worker":
                        from agent.repositories.spreadsheet_execution_queue_repository import (
                            SqlSpreadsheetExecutionQueueRepository,
                        )
                        from agent.services.spreadsheet_execution_queue_service import (
                            SpreadsheetExecutionQueueService,
                        )
                        from agent.services.spreadsheet_worker_capability_service import (
                            SpreadsheetWorkerCapabilityService,
                        )
                        from agent.services.spreadsheet_worker_control_adapter import (
                            HubSpreadsheetWorkerJobLedger,
                            HubSpreadsheetWorkerLeaseControl,
                            HubSpreadsheetWorkerLeaseScheduler,
                        )
                        from agent.services.spreadsheet_worker_ingress_service import (
                            SpreadsheetWorkerIngressService,
                        )

                        queue = SqlSpreadsheetExecutionQueueRepository(db_engine=engine)
                        worker_id = str(app.config.get("ANANTA_SPREADSHEET_WORKER_ID") or "spreadsheet-worker")
                        app.extensions["spreadsheet_proposal_execution_service"] = SpreadsheetExecutionQueueService(
                            saga=saga,
                            queue=queue,
                            worker_jobs=HubSpreadsheetWorkerJobLedger(),
                            leases=HubSpreadsheetWorkerLeaseScheduler(),
                            worker_id=worker_id,
                        )
                        app.extensions["spreadsheet_execution_ingress_service"] = SpreadsheetWorkerIngressService(
                            queue=queue,
                            saga=saga,
                            artifacts=artifact_store,
                            leases=HubSpreadsheetWorkerLeaseControl(),
                            capabilities=SpreadsheetWorkerCapabilityService(
                                signing_secret=str(app.config.get("SECRET_KEY") or settings.secret_key or "")
                            ),
                        )
                    learning_service = SpreadsheetLearningService(
                        documents=document_store,
                        store=learning_store,
                        dataset_root=state.parent / "spreadsheet-datasets",
                    )
                    app.extensions["spreadsheet_learning_service"] = learning_service
                    app.extensions["spreadsheet_training_admission_service"] = SpreadsheetTrainingAdmissionService(
                        learning=learning_service,
                        repository=learning_store,
                    )
                    from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
                    from agent.services.ml_intern_lora_inference_service import resolve_lora_storage_config

                    lora_storage = resolve_lora_storage_config(dict(app.config.get("AGENT_CONFIG", {}) or {}))
                    app.extensions["spreadsheet_adapter_admission_service"] = SpreadsheetAdapterAdmissionService(
                        registry=MlInternAdapterRegistryService(lora_storage["registry_path"])
                    )
                except (RuntimeError, ValueError):
                    status = SpreadsheetStudioWiringStatus(False, "spreadsheet_worker_configuration_invalid")
                else:
                    status = SpreadsheetStudioWiringStatus(True, None)
            else:
                status = SpreadsheetStudioWiringStatus(False, "spreadsheet_studio_disabled")
    app.extensions["spreadsheet_studio_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["SpreadsheetStudioWiringStatus", "initialize_spreadsheet_studio"]
