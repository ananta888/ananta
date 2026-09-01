from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MERGE_REVISION = "b35ae1f2c4d6"
PARENTS = {"4f9c2a7e1b6d", "a249d0e1f2a3"}
LINEAR_SUCCESSORS = {
    "c46bf2a3d5e7": MERGE_REVISION,
    "d57cf3b4e6f8": "c46bf2a3d5e7",
    "e68df4c5b7a9": "d57cf3b4e6f8",
    "a7c9e1f3b5d7": "e68df4c5b7a9",
    "b8d0f2a4c6e8": "a7c9e1f3b5d7",
}
KNOWN_DESCENDANT_REVISION = "b9d1f3a5c7e0"


def test_migration_graph_has_one_merge_head_with_both_independent_parents() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    heads = scripts.get_heads()
    assert len(heads) == 1
    revisions_on_head_chain = {revision.revision for revision in scripts.walk_revisions(base="base", head=heads[0])}
    assert KNOWN_DESCENDANT_REVISION in revisions_on_head_chain
    merge = scripts.get_revision(MERGE_REVISION)
    assert merge is not None
    down_revisions = merge.down_revision
    assert set(down_revisions if isinstance(down_revisions, tuple) else (down_revisions,)) == PARENTS
    for parent in PARENTS:
        assert scripts.get_revision(parent) is not None

    for revision, predecessor in LINEAR_SUCCESSORS.items():
        successor = scripts.get_revision(revision)
        assert successor is not None
        assert successor.down_revision == predecessor


def test_merge_upgrade_and_downgrade_are_schema_neutral() -> None:
    migration = importlib.import_module("migrations.versions.b35ae1f2c4d6_merge_sfu_security_and_speech_mutations")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE merge_sentinel (id INTEGER PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(connection))
        before = set(sa.inspect(connection).get_table_names())

        migration.upgrade()
        assert set(sa.inspect(connection).get_table_names()) == before

        migration.downgrade()
        assert set(sa.inspect(connection).get_table_names()) == before
