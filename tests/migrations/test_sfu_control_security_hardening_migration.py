from __future__ import annotations

import importlib.util
from pathlib import Path


def test_security_hardening_revision_is_linear_and_contains_bounded_state_columns() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/a249d0e1f2a3_harden_sfu_control_security.py"
    )
    spec = importlib.util.spec_from_file_location("a249_security", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "a249d0e1f2a3"
    assert module.down_revision == "9138c9d0e1f2"
    source = path.read_text(encoding="utf-8")
    for required in (
        "expires_at",
        "recipient_digest_key_id",
        "authorization_ciphertext",
        "operation_id",
        "delivery_state",
    ):
        assert required in source
