from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_worker_directory import (
    RegisteredKnowledgeIndexWorkerDirectory,
)


class _Agents:
    def __init__(self, values):
        self._values = list(values)

    def get_all(self):
        return list(self._values)


def _worker(**overrides):
    values = {
        "name": "worker-index-01",
        "url": "http://worker-index-01:5001",
        "role": "worker",
        "registration_validated": True,
        "status": "online",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_bound_worker_directory_resolves_one_validated_registry_identity():
    directory = RegisteredKnowledgeIndexWorkerDirectory(
        _Agents([_worker()])
    )

    assert directory.resolve_worker_url("worker-index-01") == (
        "http://worker-index-01:5001"
    )


@pytest.mark.parametrize(
    "workers",
    [
        [],
        [_worker(), _worker(url="http://duplicate:5002")],
        [_worker(registration_validated=False)],
        [_worker(role="hub")],
        [_worker(status="offline")],
        [_worker(url="file:///tmp/worker")],
    ],
)
def test_bound_worker_directory_rejects_missing_ambiguous_or_invalid_worker(
    workers,
):
    directory = RegisteredKnowledgeIndexWorkerDirectory(_Agents(workers))

    with pytest.raises(
        ValueError,
        match="knowledge_index_assignment_worker_unavailable",
    ):
        directory.resolve_worker_url("worker-index-01")
