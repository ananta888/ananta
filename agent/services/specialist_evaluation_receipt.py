"""Technical Hub job references; these are not production Evidence identities."""

from collections.abc import Sequence
from dataclasses import dataclass

from agent.services.specialist_prediction_artifact import SpecialistPredictionOutputs


@dataclass(frozen=True)
class SpecialistEvaluationReceipt:
    job_id: str
    job_version: int
    attempt_id: str
    dataset_id: str
    dataset_version: int
    adapter_id: str
    adapter_sha256: str
    base_model_sha256: str | None
    request_digest: str


@dataclass(frozen=True)
class SpecialistEvaluationResult:
    outputs: SpecialistPredictionOutputs
    receipt: SpecialistEvaluationReceipt


@dataclass(frozen=True)
class SpecialistPartitionOutputs(Sequence[str]):
    outputs: tuple[str, ...]
    receipt: SpecialistEvaluationReceipt

    def __len__(self) -> int:
        return len(self.outputs)

    def __getitem__(self, index):
        return self.outputs[index]
