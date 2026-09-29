from __future__ import annotations

import importlib.util
from pathlib import Path


def test_security_state_migration_has_expected_single_head_and_tables() -> None:
    path = Path("migrations/versions/6e05f6a7b8c9_add_sfu_group_key_identity_retention.py")
    spec = importlib.util.spec_from_file_location("sfu_security_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "6e05f6a7b8c9"
    assert module.down_revision == "5df4e5f6a7b8"
    source = path.read_text(encoding="utf-8")
    for table in (
        "sfu_audience_snapshot_tombstones",
        "sfu_broadcast_group_key_authorizations",
        "sfu_broadcast_group_key_packages",
        "sfu_broadcast_group_key_receipts",
        "sfu_broadcast_vendor_identities",
        "sfu_broadcast_destination_handles",
    ):
        assert table in source
