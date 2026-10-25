"""The local Laya decision server (``POST /v1/systemone``) as a decision provider (DPRV).

Laya speaks TypeSafe Jev's wire protocol, so the Jev request/response codec is
reused unchanged; this adapter only owns what differs for a local serve: the
endpoint policy (allowed hosts, no redirects), the optional shared key
(``LAYA_API_KEY``; a key-less server accepts any bearer), the provider identity
and Laya's model routing hint. A provider only proposes; policy, approval and
security gates stay with the Hub and are never decided here.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from agent.services.decision_providers.local_http import NoRedirect, validate_local_endpoint
from agent.services.decision_providers.types import DecisionProviderError
from agent.services.decision_providers.typesafe import RetryPolicy, TypeSafeJevProvider, read_api_key

DEFAULT_MODEL = ""  # the server's routed default checkpoint


def _env_api_key() -> str | None:
    return read_api_key({
        "TYPESAFE_API_KEY": os.environ.get("LAYA_API_KEY", ""),
        "TYPESAFE_API_KEY_FILE": os.environ.get("LAYA_API_KEY_FILE", ""),
    })


class LayaDecisionProvider(TypeSafeJevProvider):
    """Jev's wire against a local ``laya-serve``; same answers, local trust policy."""

    provider_id = "laya"

    def __init__(
        self,
        *,
        base_url: str,
        model: str = DEFAULT_MODEL,
        allowed_hosts: list[str],
        api_key: Callable[[], str | None] | None = None,
        timeout_seconds: float = 5.0,
        max_retries: int = 1,
        max_state_chars: int = 32_000,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(allowed_hosts, list)
            or not allowed_hosts
            or any(not isinstance(h, str) for h in allowed_hosts)
        ):
            raise DecisionProviderError("decision_provider_hosts_invalid:laya")
        if type(max_state_chars) is not int or not 1_000 <= max_state_chars <= 50_000:
            raise DecisionProviderError("decision_provider_state_limit_invalid:laya")
        self._max_state_chars = max_state_chars
        key = api_key or _env_api_key
        super().__init__(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            retry=RetryPolicy(max_retries=max_retries),
            # A key-less laya-serve accepts any bearer; only a configured key
            # must actually travel.
            api_key=lambda: key() or "laya-local",
            opener=opener or urllib.request.build_opener(NoRedirect()).open,
            endpoint_validator=lambda url: validate_local_endpoint(url, allowed_hosts),
            sleep=sleep,
            clock=clock,
        )

    def __repr__(self) -> str:  # never the key
        return f"LayaDecisionProvider(url={self._url!r}, model={self._model!r})"

    def is_available(self) -> tuple[bool, str]:
        from urllib.parse import urlsplit

        probe = f"{urlsplit(self._url).scheme}://{urlsplit(self._url).netloc}/health"
        try:
            with self._opener(urllib.request.Request(probe), timeout=2.0) as response:
                return (True, "ok") if response.status == 200 else (False, "server_unhealthy")
        except (urllib.error.URLError, OSError):
            return False, "server_unreachable"

    def decide(self, request, *, timeout_seconds: float | None = None):
        if len(request.state) > self._max_state_chars:
            raise DecisionProviderError("request_too_large")
        return super().decide(request, timeout_seconds=timeout_seconds)
