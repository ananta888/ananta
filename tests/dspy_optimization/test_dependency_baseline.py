from __future__ import annotations

import hashlib
import json
from pathlib import Path

from worker.optimization.dspy.runtime_security import DspyRuntimeSecurityPolicy

ROOT = Path(__file__).resolve().parents[2]


class FakeDspy:
    def __init__(self) -> None:
        self.configuration: dict[str, object] | None = None

    def configure_cache(self, **configuration: object) -> None:
        self.configuration = configuration


def test_runtime_security_policy_disables_diskcache_pickle_persistence() -> None:
    module = FakeDspy()

    DspyRuntimeSecurityPolicy.apply(module)

    assert module.configuration == {
        "enable_disk_cache": False,
        "enable_memory_cache": True,
        "memory_max_entries": 4096,
    }


def test_dependency_baseline_binds_upstream_artifacts_and_hashed_lock() -> None:
    baseline = json.loads((ROOT / "config/licenses/dspy-optimization.v1.json").read_text(encoding="utf-8"))
    lock = ROOT / baseline["dependency_lock"]["path"]
    lock_text = lock.read_text(encoding="utf-8")

    assert baseline["upstream"]["commit"] == "29448ae12756abdd14bd8796c819247ebb83673c"
    assert baseline["upstream"]["wheel_sha256"] in lock_text
    assert "dspy==3.2.1" in lock_text
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == baseline["dependency_lock"]["sha256"]
    assert any(
        dependency["name"] == "diskcache" and dependency["security_status"] == "known_vulnerability_mitigated"
        for dependency in baseline["direct_dependencies"]
    )


def test_worker_image_installs_only_the_hash_locked_dspy_dependency_set() -> None:
    dockerfile = (ROOT / "docker/compose-next/Dockerfile.dspy-optimization-worker").read_text(encoding="utf-8")

    assert "--require-hashes" in dockerfile
    assert "requirements.dspy-optimization.lock" in dockerfile
    assert "--requirement docker/compose-next/requirements.dspy-optimization.txt" not in dockerfile
