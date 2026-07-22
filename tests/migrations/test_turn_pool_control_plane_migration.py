import runpy
from pathlib import Path


def test_turn_pool_control_plane_migration_extends_current_head():
    migration = runpy.run_path(
        Path(__file__).resolve().parents[2]
        / "migrations/versions/7f16a7b8c9d0_add_turn_pool_control_plane.py"
    )

    assert migration["revision"] == "7f16a7b8c9d0"
    assert migration["down_revision"] == "6e05f6a7b8c9"
