import logging
from typing import Any, Optional

from agent.config import settings
from agent.llm_strategies.base import LLMStrategy
from agent.utils import _http_post


class LMStudioStrategy(LLMStrategy):
    def execute(
        self,
        model: str,
        prompt: str,
        url: str,
        api_key: Optional[str],
        history: Optional[list],
        timeout: int,
        temperature: Optional[float] = None,
        max_context_tokens: Optional[int] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        base_url = url
        base_url_lower = (base_url or "").lower()

        if "/v1/chat/completions" in base_url_lower:
            is_chat = True
        elif "/v1/completions" in base_url_lower:
            is_chat = False
        else:
            is_chat = settings.lmstudio_api_mode.lower() != "completions"

        requested_model = (model or "").strip().lower()
        candidates = self._list_lmstudio_candidates(base_url, timeout)

        if candidates:
            hist = self._prepare_lmstudio_history(candidates)

        if not requested_model or requested_model == "auto":
            hist = self._load_lmstudio_history()
            model_info = self._select_best_lmstudio_model(candidates, hist) if candidates else None
            if not model_info and candidates:
                model_info = candidates[0]
        else:
            model_info = next((c for c in candidates if c.get("id") == model), None) if candidates else None
            if not model_info and model:
                model_info = {"id": model}

        lmstudio_model = (model_info or {}).get("id") or settings.default_model
        if not lmstudio_model:
            logging.error("LM Studio model nicht gesetzt und /v1/models nicht erreichbar.")
            return ""

        if "/v1" in base_url_lower and any(e in base_url_lower for e in ["completions", "chat"]):
            request_url = base_url
        else:
            request_url = base_url.rstrip("/") + ("/chat/completions" if is_chat else "/completions")

        if candidates:
            attempted = set()
            max_attempts = min(3, len(candidates))
            current = model_info
            for _ in range(max_attempts):
                if not current or not current.get("id"):
                    break
                mid = current.get("id")
                mctx = current.get("context_length")
                result = self._call_with_model(
                    mid,
                    mctx,
                    prompt,
                    request_url,
                    is_chat,
                    history,
                    timeout,
                    temperature,
                    max_context_tokens,
                    tools,
                    tool_choice,
                    idempotency_key,
                )
                if str(result).strip():
                    return result
                attempted.add(mid)
                remaining = [c for c in candidates if c.get("id") not in attempted]
                if not remaining:
                    break
                hist = self._load_lmstudio_history()
                current = self._select_best_lmstudio_model(remaining, hist) or remaining[0]
            return ""

        return self._call_with_model(
            lmstudio_model,
            (model_info or {}).get("context_length"),
            prompt,
            request_url,
            is_chat,
            history,
            timeout,
            temperature,
            max_context_tokens,
            tools,
            tool_choice,
            idempotency_key,
        )

    def _call_with_model(
        self,
        model_id,
        model_context,
        prompt,
        request_url,
        is_chat,
        history,
        timeout,
        temperature=None,
        max_context_tokens=None,
        tools=None,
        tool_choice=None,
        idempotency_key=None,
    ):
        max_tokens = 1024
        temp = 0.2 if temperature is None else float(temperature)
        context_limit = max_context_tokens or model_context or settings.lmstudio_max_context_tokens

        if is_chat:
            messages = self._build_chat_messages(prompt, history)
            if context_limit:
                messages = self._trim_messages(messages, context_limit, max_tokens)
            payload = {
                "model": model_id,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": temp,
            }
            if tools:
                payload["tools"] = tools
                if tool_choice:
                    payload["tool_choice"] = tool_choice
        else:
            full_prompt = self._build_history_prompt(prompt, history)
            if context_limit:
                max_input = max(context_limit - max_tokens - 256, 256)
                if self._estimate_tokens(full_prompt) > max_input:
                    full_prompt = self._truncate_text(full_prompt, max_input, keep="end")
            payload = {
                "model": model_id,
                "prompt": full_prompt,
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": temp,
            }

        resp = self._post_lmstudio(request_url, payload, timeout, idempotency_key)

        # Fallback Logic (simplified from original for brevity but keeping core)
        if resp is None and is_chat:
            fallback_url = request_url.replace("/chat/completions", "/completions")
            payload_f = {
                "model": model_id,
                "prompt": self._build_history_prompt(prompt, history),
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": temp,
            }
            resp = self._post_lmstudio(fallback_url, payload_f, timeout, idempotency_key)

        result_text = ""
        usage = {}
        if isinstance(resp, dict):
            result_text = self._extract_lmstudio_text(resp)
            usage = self._extract_lmstudio_usage(resp)
        elif isinstance(resp, str):
            result_text = resp

        self._update_lmstudio_history(model_id, bool(str(result_text).strip()))
        return {"text": result_text, "usage": usage}

    def _post_lmstudio(self, url, payload, timeout, idempotency_key=None):
        resp = _http_post(
            url, payload, timeout=timeout, return_response=True, silent=True, idempotency_key=idempotency_key
        )
        if resp and hasattr(resp, "status_code") and resp.status_code < 400:
            try:
                return resp.json()
            except (ValueError, TypeError):
                return resp.text
        return None

    def _list_lmstudio_candidates(self, base_url, timeout):
        from agent.llm_integration import _list_lmstudio_candidates

        return _list_lmstudio_candidates(base_url, timeout)

    def _extract_lmstudio_text(self, payload):
        from agent.llm_integration import _extract_lmstudio_text

        return _extract_lmstudio_text(payload)

    def _extract_lmstudio_usage(self, payload):
        from agent.llm_integration import _extract_lmstudio_usage

        return _extract_lmstudio_usage(payload)

    def _load_lmstudio_history(self):
        from agent.llm_integration import _load_lmstudio_history

        return _load_lmstudio_history()

    def _select_best_lmstudio_model(self, candidates, history):
        from agent.llm_integration import _select_best_lmstudio_model

        return _select_best_lmstudio_model(candidates, history)

    def _touch_lmstudio_models(self, history, ids):
        from agent.llm_integration import _touch_lmstudio_models

        return _touch_lmstudio_models(history, ids)

    def _prepare_lmstudio_history(self, candidates):
        from agent.llm_integration import _prepare_lmstudio_history

        return _prepare_lmstudio_history(candidates)

    def _save_lmstudio_history(self, history):
        from agent.llm_integration import _save_lmstudio_history

        _save_lmstudio_history(history)

    def _update_lmstudio_history(self, model_id, success):
        from agent.llm_integration import _update_lmstudio_history

        _update_lmstudio_history(model_id, success)
