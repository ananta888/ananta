from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from tests.worker.test_backend_config_compiler import _context
from worker.training.backends.autotrain import AutoTrainTrainingBackend
from worker.training.backends.axolotl import AxolotlTrainingBackend
from worker.training.backends.base import TrainingBackendError
from worker.training.backends.llamafactory import LlamaFactoryTrainingBackend
from worker.training.backends.torchtune import TorchtuneTrainingBackend
from worker.training.external_process import ExternalProcessPort


class _SuccessfulRunner(ExternalProcessPort):
    def __init__(self, executable: Path) -> None:
        self.executable = executable
        self.command: tuple[str, ...] = ()

    def run(self, command: Sequence[str], *, cwd: Path, cancel, deadline_epoch_ms: int) -> None:  # type: ignore[no-untyped-def]
        self.command = tuple(command)
        assert Path(self.command[0]) == self.executable
        assert cwd.is_dir()
        assert not cancel.cancelled
        assert deadline_epoch_ms > 0
        artifact_root = cwd / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "adapter_model.safetensors").write_bytes(b"safe-adapter")
        (artifact_root / "adapter_config.json").write_text('{"r":16}', encoding="utf-8")
        (artifact_root / "trainer_state.json").write_text('{"train_loss":0.25}', encoding="utf-8")


@pytest.mark.parametrize(
    ("factory", "version", "expected_command"),
    [
        (AxolotlTrainingBackend, "0.18.0", "train"),
        (LlamaFactoryTrainingBackend, "0.9.5", "train"),
        (AutoTrainTrainingBackend, "0.8.36", "--config"),
        (TorchtuneTrainingBackend, "0.6.1", "run"),
    ],
)
def test_external_backend_projects_normalized_artifacts(
    tmp_path: Path,
    factory,
    version: str,
    expected_command: str,
) -> None:  # type: ignore[no-untyped-def]
    executable = tmp_path / "trainer"
    executable.write_text("", encoding="utf-8")
    runners: list[_SuccessfulRunner] = []

    def runner_factory(path: Path) -> _SuccessfulRunner:
        runner = _SuccessfulRunner(path)
        runners.append(runner)
        return runner

    backend = factory(
        package_version=lambda _name: version,
        executable_resolver=lambda _name: str(executable),
        runner_factory=runner_factory,
    )
    context = _context(tmp_path, backend.name)
    prepared = backend.prepare(context)
    trained = backend.train(context, prepared)
    metrics = backend.evaluate(context, prepared, trained)
    outcome = backend.save(context, prepared, trained, metrics)

    assert runners[0].command[1] == expected_command
    assert {path.name for path in outcome.artifacts} >= {
        "adapter_config.json",
        "adapter_model.safetensors",
        "ananta-backend-manifest.json",
        "backend-config.json",
        "evaluation.json",
    }
    assert outcome.metrics["backend"] == backend.name
    capability = backend.capability()
    assert capability.available is True
    assert capability.backend_version == version


def test_external_backend_version_mismatch_is_isolated(tmp_path: Path) -> None:
    backend = AxolotlTrainingBackend(
        package_version=lambda _name: "0.17.0",
        executable_resolver=lambda _name: str(tmp_path / "trainer"),
    )
    available, detail = backend.availability()
    assert available is False
    assert detail == "version mismatch: expected 0.18.0, observed 0.17.0"
    with pytest.raises(TrainingBackendError) as error:
        backend.prepare(_context(tmp_path, "axolotl"))
    assert error.value.code == "version_mismatch"


def test_torchtune_request_cannot_choose_recipe() -> None:
    backend = TorchtuneTrainingBackend(
        package_version=lambda _name: "0.6.1",
        executable_resolver=lambda _name: "/usr/bin/tune",
    )
    assert backend.name == "torchtune"
