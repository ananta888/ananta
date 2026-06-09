from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from agent.services.browser_task_contract import BrowserTaskContract

# RFC1918 + loopback + link-local Ranges, die niemals ein Browser-Agent kontaktieren darf
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
]


@dataclass(frozen=True)
class BrowserPolicyDecision:
    allow: bool
    reason_code: str


class BrowserPolicyService:
    def enforce_domain(self, *, url: str, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        host = (urlparse(url).hostname or "").lower()
        if not contract.allowed_domains:
            return BrowserPolicyDecision(False, "browser_policy_allowed_domains_missing")
        for pattern in contract.allowed_domains:
            p = pattern.lstrip("*.")
            if host == p or host.endswith(f".{p}"):
                return BrowserPolicyDecision(True, "ok")
        return BrowserPolicyDecision(False, "browser_policy_domain_not_allowed")

    def enforce_action_budget(self, *, action_count: int, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        if action_count > contract.max_actions:
            return BrowserPolicyDecision(False, "browser_policy_action_budget_exceeded")
        return BrowserPolicyDecision(True, "ok")

    def enforce_download_policy(self, *, download_url: str, output_path: str, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        if contract.download_policy == "deny":
            return BrowserPolicyDecision(False, "browser_policy_download_denied")

        host = (urlparse(download_url).hostname or "").lower()
        if contract.download_policy == "whitelist":
            if host not in set(contract.download_allowlist):
                return BrowserPolicyDecision(False, "browser_policy_download_host_not_whitelisted")

        if contract.download_policy == "bounded_output_dir":
            if not contract.output_dir:
                return BrowserPolicyDecision(False, "browser_policy_output_dir_missing")
            out = Path(output_path).resolve()
            base = Path(contract.output_dir).resolve()
            try:
                within_base = out.is_relative_to(base)
            except Exception:
                within_base = str(out).startswith(f"{str(base).rstrip('/')}/")
            if not within_base:
                return BrowserPolicyDecision(False, "browser_policy_download_outside_output_dir")

        return BrowserPolicyDecision(True, "ok")

    def enforce_auth_usage(self, *, requested: bool, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        if requested and contract.auth_policy != "explicit_opt_in":
            return BrowserPolicyDecision(False, "browser_policy_auth_not_allowed")
        return BrowserPolicyDecision(True, "ok")

    def enforce_blocked_hosts(self, *, url: str, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        """Blockt localhost, private IP-Ranges und explizit gelistete blocked_domains."""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().strip()

        if not hostname:
            return BrowserPolicyDecision(False, "browser_policy_empty_hostname")

        # Explizit geblockter Hostname aus Contract
        for blocked in contract.blocked_domains:
            b = blocked.lstrip("*.")
            if hostname == b or hostname.endswith(f".{b}"):
                return BrowserPolicyDecision(False, "browser_policy_blocked_domain")

        # IP-Literal prüfen
        # Entfernt IPv6-Brackets: [::1] -> ::1
        ip_str = re.sub(r"^\[(.+)\]$", r"\1", hostname)
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in _PRIVATE_RANGES:
                if ip in net:
                    return BrowserPolicyDecision(False, "browser_policy_private_ip_blocked")
        except ValueError:
            pass  # kein IP-Literal, weiter

        return BrowserPolicyDecision(True, "ok")

    def enforce_session_persistence(self, *, requested: bool, contract: BrowserTaskContract) -> BrowserPolicyDecision:
        """Prüft ob Session-Persistierung erlaubt ist."""
        if requested and not contract.persist_session:
            return BrowserPolicyDecision(False, "browser_policy_session_persistence_not_allowed")
        return BrowserPolicyDecision(True, "ok")


_SERVICE = BrowserPolicyService()


def get_browser_policy_service() -> BrowserPolicyService:
    return _SERVICE
