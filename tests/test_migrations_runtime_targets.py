from pathlib import Path


def test_runtime_targets_migration_is_portable_and_has_real_downgrade():
    migration = Path(
        "migrations/versions/c6d7e8f9a0b1_add_missing_runtime_targets_and_selection_columns.py"
    ).read_text(encoding="utf-8")

    assert "'[]'::json" not in migration
    assert "server_default=sa.text(\"'[]'\")" in migration

    # downgrade must do real work, not silent pass
    assert "def downgrade()" in migration
    assert "pass" not in migration.split("def downgrade()", 1)[1]
    assert "_drop_column_if_exists(\"agents\", \"runtime_targets\")" in migration
