"""OpenAI-compatible chat endpoint (llama-server); bounded, no redirects, no proxy.

Same transport guarantees as ``ollama_http`` — they are shared through
``bounded_json_http`` — but addressed through the OpenAI API surface:
``POST {base}/chat/completions`` and ``GET {base}/models``. The base URL keeps
its version path (``http://host:8081/v1``), so endpoint validation admits a
bounded, traversal-free path here.
"""

import os
import time
import urllib.request
from typing import Protocol

from worker.meet_media.bounded_json_http import BoundedJsonClient, NoRedirect, endpoint

DEFAULT_BASE_URL = "http://127.0.0.1:8081/v1"
# Matches the Ollama chat budget: the room turn has the same deadline either way.
CHAT_BUDGET_SECONDS = 60
MODELS_BUDGET_SECONDS = 5
MAX_API_KEY_BYTES = 512


class OpenAiJsonPort(Protocol):
    def chat(self, payload: dict) -> dict: ...
    def models(self) -> dict: ...


def _authorization():
    """Bearer header only when an operator configured a key.

    The key is read once per client, never echoed into a payload, an URL or a
    failure message; transport failures stay redacted to one code.
    """
    key = os.environ.get("MEET_LLM_OPENAI_API_KEY", "").strip()
    if not key:
        return {}
    if len(key) > MAX_API_KEY_BYTES or any(ord(c) <= 32 or ord(c) == 127 for c in key):
        raise ValueError("meet_llm_api_key_invalid")
    return {"Authorization": "Bearer " + key}


class OpenAiHttp:
    def __init__(self, base_url=None, *, opener=None, clock=time.monotonic, headers=None):
        self.endpoint = endpoint(
            os.environ.get("MEET_LLM_OPENAI_BASE_URL", DEFAULT_BASE_URL) if base_url is None else base_url,
            allow_path=True,
        )
        self.opener = (
            opener
            if opener is not None
            else urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        )
        self.clock = clock
        self._client = BoundedJsonClient(
            self.endpoint,
            opener=self.opener,
            clock=clock,
            headers=_authorization() if headers is None else headers,
        )

    def chat(self, payload):
        return self._client.exchange("/chat/completions", payload, CHAT_BUDGET_SECONDS)

    def models(self):
        return self._client.exchange("/models", None, MODELS_BUDGET_SECONDS)
