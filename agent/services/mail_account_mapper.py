from __future__ import annotations

from typing import Any, Mapping

from agent.services.mail_contract_service import MailAccountV2


class MailAccountMapper:
    @staticmethod
    def from_legacy_imap(payload: Mapping[str, Any]) -> MailAccountV2:
        source = dict(payload)
        imap_config = {
            "host": str(source.get("host") or "").strip(),
            "port": int(source.get("port") or 993),
            "tls_mode": str(source.get("tls_mode") or "require_tls"),
            "auth_mode": str(source.get("auth_mode") or "password_app_token"),
        }
        return MailAccountV2.from_mapping(
            {
                "account_id": str(source.get("account_id") or "").strip(),
                "display_name": str(source.get("display_name") or "").strip(),
                "requested_protocol": "imap",
                "resolved_protocol": "imap",
                "username_ref": str(source.get("username_ref") or "").strip(),
                "credential_ref": str(source.get("credential_ref") or "").strip(),
                "sync_policy": str(source.get("sync_policy") or "manual"),
                "enabled": bool(source.get("enabled", True)),
                "provider_config": {"imap": imap_config},
            }
        )

    @staticmethod
    def to_legacy_imap_runtime_config(account: MailAccountV2) -> dict[str, Any]:
        config = dict(account.provider_config.get("imap") or account.provider_config)
        return {
            "account_id": account.account_id,
            "display_name": account.display_name,
            "host": str(config.get("host") or ""),
            "port": int(config.get("port") or 993),
            "username_ref": account.username_ref,
            "credential_ref": account.credential_ref,
            "auth_mode": str(config.get("auth_mode") or "password_app_token"),
            "tls_mode": str(config.get("tls_mode") or "require_tls"),
            "sync_policy": account.sync_policy,
            "enabled": account.enabled,
        }

    @staticmethod
    def normalize(payload: Mapping[str, Any]) -> MailAccountV2:
        if "host" in payload and "provider_config" not in payload:
            return MailAccountMapper.from_legacy_imap(payload)
        return MailAccountV2.from_mapping(payload)
