from __future__ import annotations

import pytest

from agent.services.model_inference_adapters import CAP_CLASSIFICATION, CAP_RERANK
from agent.services.model_inference_adapters.huggingface_transformers_adapter import (
    HuggingFaceTransformersAdapter,
)


@pytest.mark.parametrize(
    ("labels", "rerank_expected"),
    [
        (("negative", "positive"), True),
        (("negative", "neutral", "positive"), False),
        (("only-label",), False),
    ],
)
def test_rerank_requires_exactly_two_resolved_labels(
    labels: tuple[str, ...],
    rerank_expected: bool,
) -> None:
    adapter = object.__new__(HuggingFaceTransformersAdapter)
    adapter._task = "sequence-classification"
    adapter._labels = labels

    capabilities = adapter._task_capabilities()

    assert CAP_CLASSIFICATION in capabilities
    assert (CAP_RERANK in capabilities) is rerank_expected
