from __future__ import annotations

from agent.services.imap_account_service import (
    create_imap_account,
    delete_imap_account,
    disable_imap_account,
    list_imap_accounts,
)


def test_imap_account_create_list_disable_delete_cycle(tmp_path) -> None:
    created = create_imap_account(
        repo_root=tmp_path,
        display_name="Work",
        host="imap.example.com",
        port=993,
        username_ref="user://alice",
        credential_ref="secret://imap/alice",
    )
    assert created["account_id"]
    listed = list_imap_accounts(repo_root=tmp_path)
    assert len(listed) == 1
    assert "password" not in listed[0]

    disabled = disable_imap_account(repo_root=tmp_path, account_id=created["account_id"])
    assert disabled["enabled"] is False

    deleted = delete_imap_account(repo_root=tmp_path, account_id=created["account_id"])
    assert deleted["account_id"] == created["account_id"]
    assert list_imap_accounts(repo_root=tmp_path) == []
