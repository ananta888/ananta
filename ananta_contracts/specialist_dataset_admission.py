"""Dataset admission metadata, never a source or run evidence identity."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AdmittedSpecialistDataset:
    dataset_id: str
    version: int

    def __post_init__(self):
        if (
            not isinstance(self.dataset_id, str)
            or not self.dataset_id.strip()
            or type(self.version) is not int
            or not 1 <= self.version <= 2**63 - 1
        ):
            raise ValueError("specialist_dataset_admission_invalid")
