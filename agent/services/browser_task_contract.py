from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ALLOWED_DOWNLOAD_POLICY = {"deny", "whitelist", "bounded_output_dir"}
_ALLOWED_AUTH_POLICY = {"none", "explicit_opt_in"}
_ALLOWED_SCREENSHOT_POLICY = {"none", "on_error", "always"}


@dataclass(frozen=True)
class BrowserTaskContract:
    allowed_domains: tuple[str, ...]
    max_actions: int
    timeout_seconds: int
    download_policy: str
    auth_policy: str
    screenshot_policy: str
    download_allowlist: tuple[str, ...]
    output_dir: str | None

    @staticmethod
    def from_payload(payload: dict[str, Any] | None) -> "BrowserTaskContract":
        data = dict(payload or {})
        domains = tuple(str(x).strip().lower() for x in list(data.get("allowed_domains") or []) if str(x).strip())
        max_actions = int(data.get("max_actions") or 10)
        timeout_seconds = int(data.get("timeout_seconds") or 120)
        download_policy = str(data.get("download_policy") or "deny").strip().lower()
        auth_policy = str(data.get("auth_policy") or "none").strip().lower()
        screenshot_policy = str(data.get("screenshot_policy") or "none").strip().lower()
        download_allowlist = tuple(str(x).strip().lower() for x in list(data.get("download_allowlist") or []) if str(x).strip())
        output_dir = str(data.get("output_dir") or "").strip() or None

        if max_actions <= 0:
            raise ValueError("browser_contract_invalid_max_actions")
        if timeout_seconds <= 0:
            raise ValueError("browser_contract_invalid_timeout_seconds")
        if download_policy not in _ALLOWED_DOWNLOAD_POLICY:
            raise ValueError("browser_contract_invalid_download_policy")
        if auth_policy not in _ALLOWED_AUTH_POLICY:
            raise ValueError("browser_contract_invalid_auth_policy")
        if screenshot_policy not in _ALLOWED_SCREENSHOT_POLICY:
            raise ValueError("browser_contract_invalid_screenshot_policy")

        return BrowserTaskContract(
            allowed_domains=domains,
            max_actions=max_actions,
            timeout_seconds=timeout_seconds,
            download_policy=download_policy,
            auth_policy=auth_policy,
            screenshot_policy=screenshot_policy,
            download_allowlist=download_allowlist,
            output_dir=output_dir,
        )
