"""Optional execution-only verification adapters for delegated Worker runs."""

from worker.verification.adapters import (
    CrossHairCheckAdapter,
    CrossHairCoverAdapter,
    CrossHairDiffBehaviorAdapter,
    HypothesisCrossHairBackendAdapter,
    PytestHypothesisRunnerAdapter,
)
from worker.verification.ports import (
    BehaviorDiffRunnerPort,
    CounterexampleMaterializerPort,
    PropertyTestRunnerPort,
    SymbolicContractCheckerPort,
)

__all__ = [
    "BehaviorDiffRunnerPort",
    "CounterexampleMaterializerPort",
    "CrossHairCheckAdapter",
    "CrossHairCoverAdapter",
    "CrossHairDiffBehaviorAdapter",
    "HypothesisCrossHairBackendAdapter",
    "PropertyTestRunnerPort",
    "PytestHypothesisRunnerAdapter",
    "SymbolicContractCheckerPort",
]
