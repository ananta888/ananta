"""Worker side of path A (WCRB-008): CodeCompass tool calls routed to the Hub gateway.

Which path a CodeCompass tool call takes is configured in the worker tool loop
(``codecompass_access``, per tool ``codecompass_access_overrides``, optional
``codecompass_access_fallback = "hub"``):

- ``delegated``: the worker runs the tool itself under the Hub-signed capability
  it received with the step (path B);
- ``hub``: the Hub runs the tool for the task (path A, this module);
- ``off``: CodeCompass tools are refused.

A gateway failure is a coded tool error for the model, never a local fallback.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

GATEWAY_PATH = "/api/worker/v1/tasks/{task_id}/tools/{tool_name}"
MAX_RESPONSE_BYTES = 512 * 1024


def _error(tool_name: str, tool_call_id: str, error: str) -> dict[str, Any]:
    from agent.services.tools._evidence import build_tool_result

    return build_tool_result(tool_name=tool_name, tool_call_id=tool_call_id, status="error", error=error)


def _reason(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read(4096) or b"{}")
        return str((body.get("data") or {}).get("reason_code") or "")[:120]
    except (ValueError, AttributeError, OSError):
        return ""


class HubToolGatewayClient:
    def __init__(self, *, hub_url: str, headers: Mapping[str, str],
                 opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 60.0) -> None:
        self._hub = str(hub_url or "").rstrip("/")
        self._headers = {**dict(headers), "Content-Type": "application/json"}
        self._opener = opener
        self._timeout = float(timeout)

    def execute(self, *, task_id: str, tool_name: str, arguments: Mapping[str, Any],
                tool_call_id: str) -> dict[str, Any]:
        if not task_id:
            return _error(tool_name, tool_call_id, "hub_gateway_task_required")
        if not self._hub.startswith(("http://", "https://")):
            return _error(tool_name, tool_call_id, "hub_gateway_url_invalid")
        url = self._hub + GATEWAY_PATH.format(task_id=urllib.parse.quote(task_id, safe=""),
                                              tool_name=urllib.parse.quote(tool_name, safe=""))
        body = json.dumps({"arguments": dict(arguments), "tool_call_id": tool_call_id}).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=self._headers, method="POST")
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            reason = _reason(error)
            return _error(tool_name, tool_call_id, f"hub_gateway_{reason or 'http_' + str(error.code)}")
        except OSError:
            return _error(tool_name, tool_call_id, "hub_gateway_unreachable")
        if len(raw) > MAX_RESPONSE_BYTES:
            return _error(tool_name, tool_call_id, "hub_gateway_result_too_large")
        try:
            result = json.loads(raw)
        except ValueError:
            result = None
        if not isinstance(result, dict):
            return _error(tool_name, tool_call_id, "hub_gateway_result_invalid")
        return result


def hub_tool_gateway_client() -> HubToolGatewayClient:
    from agent.config import settings
    from agent.services.worker_hub_headers import registered_worker_hub_headers

    return HubToolGatewayClient(hub_url=str(settings.hub_url or ""), headers=registered_worker_hub_headers())
