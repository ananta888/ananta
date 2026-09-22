"""Fixed operator endpoint and bounded JSON reads; no redirects or proxy fallback.

The transport guarantees themselves live in ``bounded_json_http``; this adapter
only owns the Ollama endpoint policy, its two fixed paths and their budgets.
"""

import os
import time
import urllib.request
from typing import Protocol

from worker.meet_media.bounded_json_http import BoundedJsonClient, NoRedirect, endpoint

CHAT_BUDGET_SECONDS = 60
MODELS_BUDGET_SECONDS = 5


class OllamaJsonPort(Protocol):
    def chat(self, payload: dict) -> dict: ...
    def models(self) -> dict: ...


# Kept as module attributes: existing operators and tests pin the redirect
# handler and the endpoint policy through these names.
_NoRedirect = NoRedirect


def _endpoint(value):
    return endpoint(value)


class OllamaHttp:
    def __init__(self, endpoint=None, *, opener=None, clock=time.monotonic):
        self.endpoint = _endpoint(
            os.environ.get("MEET_OLLAMA_URL", "http://meet-ollama:11434") if endpoint is None else endpoint
        )
        self.opener = (
            opener
            if opener is not None
            else urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        )
        self.clock = clock
        self._client = BoundedJsonClient(self.endpoint, opener=self.opener, clock=clock)

    def chat(self, payload):
        return self._client.exchange("/api/chat", payload, CHAT_BUDGET_SECONDS)

    def models(self):
        return self._client.exchange("/api/ps", None, MODELS_BUDGET_SECONDS)
