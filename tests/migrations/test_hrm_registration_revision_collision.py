from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[2]


def test_hrm_and_registration_migrations_have_distinct_linear_revisions() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    registration = scripts.get_revision("d4f6a8c0e2b5")
    hrm_control_plane = scripts.get_revision("d4f6a8c0e2b6")
    receipts = scripts.get_revision("e5a7b9d1f3c6")

    assert registration is not None
    assert hrm_control_plane is not None
    assert receipts is not None
    assert hrm_control_plane.down_revision == registration.revision
    assert receipts.down_revision == hrm_control_plane.revision
