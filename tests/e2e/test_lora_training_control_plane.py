"""Canonical network-free LoRA control-plane E2E gate.

The implementation lives in the focused mock-contract module so it can also
be run independently while this stable entry point remains the release-gate
target documented for operators and CI.
"""

from __future__ import annotations

from pathlib import Path

from tests.e2e.test_lora_training_control_center_mock import (
    test_mock_training_and_existing_adapter_evaluation_cross_the_real_http_contract as _run_mock_contract,
)


def test_lora_training_control_plane_crosses_authenticated_worker_boundary(tmp_path: Path) -> None:
    _run_mock_contract(tmp_path)
