"""Worker-side client for the single admitted Unsloth Studio MCP mutation."""

from __future__ import annotations

from collections.abc import Mapping
import os
import re
from typing import Any
import uuid

from ananta_contracts.mcp_streamable_http import (
    McpStreamableHttpTransport,
)
from agent.services.opaque_secret_reference_service import (
    OpaqueSecretReferenceService,
)
from agent.services.unsloth_studio_transport import (
    UnslothStudioTransport,
    UnslothStudioTransportConfig,
)

_CORRELATION_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
)


class UnslothMcpControlError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class UnslothMcpStopTrainingClient:
    """Execute only ``stop_training`` against the pinned Studio MCP origin."""

    def __init__(
        self,
        *,
        transport: McpStreamableHttpTransport,
        bearer_secret_ref: str,
    ) -> None:
        self._transport = transport
        self._bearer_secret_ref = bearer_secret_ref

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "UnslothMcpStopTrainingClient":
        source = os.environ if env is None else env
        token = str(
            source.get("ANANTA_UNSLOTH_STUDIO_MCP_TOKEN") or ""
        )
        if (
            not 16 <= len(token) <= 4096
            or not token.isascii()
            or any(ord(character) < 0x21 for character in token)
        ):
            raise ValueError(
                "unsloth_mcp_control_secret_invalid"
            )
        secret_ref = (
            "env://ANANTA_UNSLOTH_STUDIO_MCP_TOKEN"
        )
        transport = UnslothStudioTransport(
            config=UnslothStudioTransportConfig(
                base_url=str(
                    source.get("ANANTA_UNSLOTH_STUDIO_URL")
                    or ""
                ),
                credential_secret_ref=secret_ref,
                expected_studio_version=str(
                    source.get(
                        "ANANTA_UNSLOTH_STUDIO_EXPECTED_VERSION"
                    )
                    or ""
                ),
                allowed_hosts=_csv(
                    source.get(
                        "ANANTA_UNSLOTH_STUDIO_ALLOWED_HOSTS"
                    )
                ),
                allowed_ip_cidrs=_csv(
                    source.get(
                        "ANANTA_UNSLOTH_STUDIO_ALLOWED_IP_CIDRS"
                    )
                ),
                external_network_enabled=False,
                local_network_enabled=True,
                allow_plaintext_internal=True,
            ),
            secret_resolver=OpaqueSecretReferenceService(source),
        )
        return cls(
            transport=McpStreamableHttpTransport(transport),
            bearer_secret_ref=secret_ref,
        )

    def stop_training(
        self,
        *,
        save: bool,
        correlation_id: str,
    ) -> Mapping[str, Any]:
        if _CORRELATION_ID.fullmatch(correlation_id) is None:
            raise UnslothMcpControlError(
                "unsloth_mcp_correlation_id_invalid"
            )
        request_id = f"ananta-stop-{uuid.uuid4().hex}"
        response = self._transport.request_json(
            method="POST",
            path="/mcp/",
            payload={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "stop_training",
                    "arguments": {"save": save},
                },
            },
            service_bearer_secret_ref=(
                self._bearer_secret_ref
            ),
        )
        result = response.get("result")
        if (
            response.get("jsonrpc") != "2.0"
            or response.get("id") != request_id
            or not isinstance(result, Mapping)
            or result.get("isError") is True
            or "error" in response
        ):
            raise UnslothMcpControlError(
                "unsloth_mcp_stop_training_rejected"
            )
        return {
            "schema": (
                "ananta.unsloth-mcp-control-result.v1"
            ),
            "tool_id": "stop_training",
            "correlation_id": correlation_id,
            "accepted": True,
        }


def _csv(value: object) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").split(",")
        if item.strip()
    )


__all__ = [
    "UnslothMcpControlError",
    "UnslothMcpStopTrainingClient",
]
