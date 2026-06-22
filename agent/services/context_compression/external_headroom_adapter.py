"""HCCA-012: Optional adapter that delegates compression to an external Headroom CLI/HTTP service.

Disabled by default — requires explicit config to activate.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HeadroomAdapterConfig:
    enabled: bool = False
    transport: str = "cli"  # "cli" | "http" | "mcp"
    command: list[str] = field(default_factory=lambda: ["headroom"])
    base_url: str = ""
    mcp_server: str = ""
    timeout_seconds: float = 20.0

    @classmethod
    def from_config(cls, config: dict | None = None) -> HeadroomAdapterConfig:
        if not config:
            return cls()
        return cls(
            enabled=bool(config.get("enabled", False)),
            transport=str(config.get("transport", "cli")),
            command=list(config.get("command", ["headroom"])),
            base_url=str(config.get("base_url", "")),
            mcp_server=str(config.get("mcp_server", "")),
            timeout_seconds=float(config.get("timeout_seconds", 20.0)),
        )


def _passthrough_result(
    content: str,
    reason_code: str = "external_headroom_unavailable",
) -> dict[str, Any]:
    return {
        "decision": "passthrough",
        "compressed_content": content,
        "token_before": 0,
        "token_after": 0,
        "reason_code": reason_code,
    }


class ExternalHeadroomAdapter:
    def __init__(self, config: HeadroomAdapterConfig) -> None:
        self._config = config

    def compress(
        self,
        content: str,
        content_type: str,
        budget_tokens: int = 0,
    ) -> dict[str, Any]:
        """Compress content using the external headroom service.

        Returns a dict with keys:
            decision, compressed_content, token_before, token_after, reason_code
        """
        if not self._config.enabled:
            return _passthrough_result(content, reason_code="disabled")

        transport = self._config.transport

        if transport == "cli":
            return self._compress_cli(content, content_type, budget_tokens)
        elif transport == "http":
            return self._compress_http(content, content_type, budget_tokens)
        elif transport == "mcp":
            raise NotImplementedError("HCCA-012: MCP transport not yet implemented")
        else:
            return _passthrough_result(content, reason_code="unknown_transport")

    def _compress_cli(
        self, content: str, content_type: str, budget_tokens: int
    ) -> dict[str, Any]:
        cmd = list(self._config.command)
        payload = json.dumps(
            {
                "content": content,
                "content_type": content_type,
                "budget_tokens": budget_tokens,
            }
        )
        try:
            proc = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
            )
            if proc.returncode != 0:
                return _passthrough_result(content)
            result = json.loads(proc.stdout)
            return _normalise_result(result, content)
        except Exception:
            return _passthrough_result(content)

    def _compress_http(
        self, content: str, content_type: str, budget_tokens: int
    ) -> dict[str, Any]:
        if not self._config.base_url:
            return _passthrough_result(content)
        payload = json.dumps(
            {
                "content": content,
                "content_type": content_type,
                "budget_tokens": budget_tokens,
            }
        ).encode()
        req = urllib.request.Request(
            self._config.base_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                result = json.loads(resp.read())
            return _normalise_result(result, content)
        except Exception:
            return _passthrough_result(content)

    def is_available(self) -> bool:
        """Return whether the external headroom service appears reachable."""
        transport = self._config.transport
        if transport == "cli":
            return bool(
                self._config.command and shutil.which(self._config.command[0]) is not None
            )
        elif transport == "http":
            return bool(self._config.base_url)
        return False

    def health_check(self) -> dict[str, Any]:
        """Return a health-check dict."""
        available = self._config.enabled and self.is_available()
        if not self._config.enabled:
            reason = "disabled"
        elif self._config.transport == "cli" and not shutil.which(
            self._config.command[0] if self._config.command else ""
        ):
            reason = "cli_command_not_found"
        elif self._config.transport == "http" and not self._config.base_url:
            reason = "base_url_empty"
        else:
            reason = "ok" if available else "unavailable"
        return {
            "available": available,
            "transport": self._config.transport,
            "reason": reason,
        }


def _normalise_result(result: dict[str, Any], original_content: str) -> dict[str, Any]:
    """Ensure result always has required keys, falling back to passthrough."""
    if not isinstance(result, dict):
        return _passthrough_result(original_content)
    return {
        "decision": result.get("decision", "passthrough"),
        "compressed_content": result.get("compressed_content", original_content),
        "token_before": int(result.get("token_before", 0)),
        "token_after": int(result.get("token_after", 0)),
        "reason_code": result.get("reason_code", "external_headroom"),
    }
