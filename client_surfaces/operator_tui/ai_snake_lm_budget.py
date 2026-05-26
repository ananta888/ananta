from __future__ import annotations

import time
from collections import deque
from typing import Any


class AiSnakeLmBudget:
    def __init__(
        self,
        *,
        predict_window_seconds: float = 3.0,
        max_predict_in_window: int = 1,
        max_concurrent_explain_chat: int = 1,
        max_prompt_chars: int = 4000,
    ) -> None:
        self.predict_window_seconds = max(0.5, float(predict_window_seconds))
        self.max_predict_in_window = max(1, int(max_predict_in_window))
        self.max_concurrent_explain_chat = max(1, int(max_concurrent_explain_chat))
        self.max_prompt_chars = max(32, int(max_prompt_chars))
        self._predict_times: deque[float] = deque()
        self._active_explain_chat: set[str] = set()

    def allow_predict(self, *, prompt: str, now: float | None = None) -> tuple[bool, str]:
        ts = time.time() if now is None else float(now)
        if len(prompt) > self.max_prompt_chars:
            return False, "prompt_too_large"
        self._evict_old_predicts(ts)
        if len(self._predict_times) >= self.max_predict_in_window:
            return False, "predict_rate_limited"
        self._predict_times.append(ts)
        return True, "allowed"

    def allow_explain_chat(self, *, request_id: str, prompt: str) -> tuple[bool, str]:
        if len(prompt) > self.max_prompt_chars:
            return False, "prompt_too_large"
        if len(self._active_explain_chat) >= self.max_concurrent_explain_chat:
            return False, "concurrency_limited"
        self._active_explain_chat.add(str(request_id))
        return True, "allowed"

    def finish_explain_chat(self, request_id: str) -> None:
        self._active_explain_chat.discard(str(request_id))

    def debug_state(self, *, now: float | None = None) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)
        self._evict_old_predicts(ts)
        return {
            "predict_window_seconds": self.predict_window_seconds,
            "predict_requests_in_window": len(self._predict_times),
            "max_predict_in_window": self.max_predict_in_window,
            "active_explain_chat": len(self._active_explain_chat),
            "max_concurrent_explain_chat": self.max_concurrent_explain_chat,
            "max_prompt_chars": self.max_prompt_chars,
        }

    def _evict_old_predicts(self, now: float) -> None:
        while self._predict_times and (now - self._predict_times[0]) > self.predict_window_seconds:
            self._predict_times.popleft()
