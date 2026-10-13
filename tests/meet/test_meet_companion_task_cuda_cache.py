"""Task kernel scratch survives optional utterance retirement, never its owner."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.meet.test_meet_companion_task_grant_client import assignment
from worker.meet_media.companion_process_transport import CompanionExecutionError
from worker.meet_media.companion_task_controls import CompanionSourceInterrupted
from worker.meet_media.companion_task_cuda_cache import CachedCompanionExecution, TaskCudaCache
from worker.meet_media.companion_task_model_selection import assigned_execution


def test_private_cache_is_distinct_bounded_and_removed_with_its_owner():
    first, second = TaskCudaCache(), TaskCudaCache()
    try:
        paths = [Path(cache.environment["CUDA_CACHE_PATH"]) for cache in (first, second)]
        assert paths[0] != paths[1]
        assert all(path.parent == Path("/tmp") and path.stat().st_mode & 0o777 == 0o700 for path in paths)
        assert first.environment["CUDA_CACHE_MAXSIZE"] == "33554432"
        first.close()
        assert not paths[0].exists() and paths[1].exists()
        first.close()
    finally:
        first.close()
        second.close()


def test_optional_source_retirement_keeps_cache_until_execution_stops():
    cache = TaskCudaCache()
    path = Path(cache.environment["CUDA_CACHE_PATH"])
    class Execution:
        def call(self, *args, **kwargs):
            raise CompanionSourceInterrupted("synthetic_speech_retired")

        def close(self):
            assert path.exists()

    execution = CachedCompanionExecution(Execution(), cache)
    try:
        with pytest.raises(CompanionSourceInterrupted):
            execution.call("speech", "synthetic", cooperate=lambda: None)
        assert path.exists()
    finally:
        execution.close()
    assert not path.exists()


def test_failed_execution_cleanup_still_retires_only_owned_scratch():
    cache = TaskCudaCache()
    path = Path(cache.environment["CUDA_CACHE_PATH"])
    execution = CachedCompanionExecution(SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError())), cache)
    with pytest.raises(RuntimeError):
        execution.close()
    assert not path.exists()
    with pytest.raises(CompanionExecutionError, match="meet_companion_execution_closed"):
        execution.call("speech", "synthetic", cooperate=lambda: None)


def test_factory_failure_removes_new_task_cache_and_preserves_parent_environment(monkeypatch):
    import worker.meet_media.companion_task_model_selection as selection
    client = SimpleNamespace(assignment=assignment())
    client.assignment["capabilities"] = ["chat.read", "chat.send", "speech.publish"]
    paths = []
    def fail(**options):
        paths.append(Path(options["environment"]["CUDA_CACHE_PATH"]))
        assert paths[-1].exists()
        assert options["environment"]["CUDA_CACHE_MAXSIZE"] == "33554432"
        raise RuntimeError("synthetic_constructor_failed")
    monkeypatch.setattr(selection, "CompanionExecutionProcess", fail)
    monkeypatch.setenv("CUDA_CACHE_PATH", "/synthetic/foreign-cache")
    with pytest.raises(RuntimeError):
        assigned_execution(client, {})
    assert paths and not paths[0].exists()
    assert os.environ["CUDA_CACHE_PATH"] == "/synthetic/foreign-cache"
