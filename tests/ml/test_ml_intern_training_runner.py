"""Compatibility tests for the retired Hub-side training entrypoint."""

from agent import ml_intern_training_runner as compatibility_runner
from worker.training import job_process


def test_runner_shim_delegates_to_worker_owned_entrypoint() -> None:
    assert compatibility_runner.main is job_process.main


def test_runner_shim_exposes_no_legacy_path_based_parser() -> None:
    assert not hasattr(compatibility_runner, "_load_spec")
    assert not hasattr(compatibility_runner, "_run_peft_trl")
    assert not hasattr(compatibility_runner, "_run_unsloth")
