from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.validate_training_backend_licenses import validate, validate_dependency_locks


def _register() -> dict:
    return json.loads(Path("config/licenses/training-backends.v1.json").read_text())


def test_training_backend_license_register_is_valid() -> None:
    assert validate(_register()) == []


def test_unknown_license_and_missing_source_binding_are_denied() -> None:
    payload = copy.deepcopy(_register())
    payload["backends"][0]["license_spdx"] = "LicenseRef-Unknown"
    payload["backends"][0].pop("source_commit")
    problems = validate(payload)
    assert any("license_spdx is not allowed" in item for item in problems)
    assert any("source commit or package SHA-256" in item for item in problems)


def test_every_reviewed_backend_has_a_fully_pinned_matching_dependency_lock() -> None:
    assert validate_dependency_locks(_register(), Path("docker/compose-next")) == []
