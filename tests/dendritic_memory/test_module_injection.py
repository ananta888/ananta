from __future__ import annotations

import pytest

from worker.training.dendritic.injection import inject_dendritic_modules
from worker.training.dendritic.module import build_dendritic_memory_module, parameter_report

torch = pytest.importorskip("torch")
nn = torch.nn


def test_reference_module_supports_batch_and_sequence_shapes_and_reports_parameters() -> None:
    module = build_dendritic_memory_module(
        hidden_dimension=8,
        branch_count=4,
        top_k=2,
        routing_enabled=True,
        readout="gated_residual",
        max_memory_bytes=1_048_576,
    )
    assert module(torch.zeros(2, 8)).shape == (2, 8)
    assert module(torch.zeros(2, 3, 8)).shape == (2, 3, 8)
    report = parameter_report(module)
    assert report["parameter_count"] == 65
    assert report["trainable_parameter_count"] == 65
    with pytest.raises(ValueError, match="shape_invalid"):
        module(torch.zeros(2, 7))
    with pytest.raises(ValueError, match="non_finite"):
        module(torch.full((2, 8), float("nan")))


def test_injection_is_exact_reversible_and_freezes_base_weights() -> None:
    class Body(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layers = nn.ModuleList([nn.Linear(8, 8)])

        def forward(self, value):
            return self.layers[0](value)

    class Model(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.model = Body()

        def forward(self, value):
            return self.model(value)

    model = Model()
    memory = build_dendritic_memory_module(
        hidden_dimension=8,
        branch_count=2,
        top_k=1,
        routing_enabled=False,
        readout="residual_sum",
        max_memory_bytes=1_048_576,
    )
    original = model.model.layers[0]
    handle = inject_dendritic_modules(
        model,
        targets=("model.layers.0",),
        modules={"model.layers.0": memory},
        allowed_prefixes=("model.layers.",),
    )
    assert model(torch.zeros(1, 8)).shape == (1, 8)
    assert all(not parameter.requires_grad for parameter in original.parameters())
    handle.unpatch()
    assert model.model.layers[0] is original


def test_unknown_or_non_allowlisted_target_is_rejected() -> None:
    model = nn.Sequential(nn.Linear(8, 8))
    with pytest.raises(PermissionError, match="denied"):
        inject_dendritic_modules(model, targets=("0",), modules={"0": nn.Identity()}, allowed_prefixes=("model.",))
