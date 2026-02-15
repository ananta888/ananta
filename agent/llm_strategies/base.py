from abc import ABC, abstractmethod
from typing import Optional, Any
from agent.common.errors import PermanentError, TransientError
from agent.common.http import _classify_status

class LLMStrategy(ABC):
    @abstractmethod
    def execute(
        self,
        model: str,
        prompt: str,
        url: str,
        api_key: Optional[str],
        history: Optional[list],
        timeout: int,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None,
        idempotency_key: Optional[str] = None
    ) -> Any:
        pass

    def _build_chat_messages(self, prompt: str, history: list | None) -> list:
        messages = []
        if history:
            for h in history:
                if isinstance(h, dict) and "role" in h and "content" in h:
                    messages.append({"role": h["role"], "content": h["content"]})
                elif isinstance(h, dict):
                    messages.append({"role": "user", "content": h.get("prompt") or ""})
                    assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                    messages.append({"role": "assistant", "content": assistant_msg})
                    if "output" in h:
                        messages.append({"role": "system", "content": f"Befehlsausgabe: {h.get('output')}"})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_history_prompt(self, prompt: str, history: list | None) -> str:
        full_prompt = prompt
        if history:
            history_str = "\n\nHistorie bisheriger Interaktionen:\n"
            for h in history:
                if isinstance(h, dict) and "role" in h and "content" in h:
                    role_map = {"user": "User", "assistant": "Assistant", "system": "System"}
                    role = role_map.get(h["role"], h["role"])
                    history_str += f"{role}: {h['content']}\n"
                elif isinstance(h, dict):
                    history_str += f"- Prompt: {h.get('prompt')}\n"
                    history_str += f"  Reasoning: {h.get('reason')}\n"
                    history_str += f"  Befehl: {h.get('command')}\n"
                    if "output" in h:
                        out = h.get("output", "")
                        if len(out) > 500:
                            out = out[:500] + "..."
                        history_str += f"  Ergebnis: {out}\n"
            full_prompt = history_str + "\nAktueller Auftrag:\n" + prompt
        return full_prompt

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def _truncate_text(self, text: str, max_tokens: int, keep: str = "end") -> str:
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        if keep == "start":
            return text[:max_chars]
        return text[-max_chars:]

    def _trim_messages(self, messages: list, max_context_tokens: int, max_output_tokens: int) -> list:
        budget = max(max_context_tokens - max_output_tokens - 256, 256)
        if not messages:
            return messages

        system_msg = None
        if messages and messages[0].get("role") == "system":
            system_msg = dict(messages[0])
            messages = messages[1:]

        total_tokens = 0
        for msg in messages:
            total_tokens += self._estimate_tokens(str(msg.get("content", "")))
        if system_msg:
            total_tokens += self._estimate_tokens(str(system_msg.get("content", "")))
        if total_tokens <= budget:
            return [system_msg] + messages if system_msg else messages

        trimmed_messages = []
        remaining = budget
        if system_msg:
            system_tokens = self._estimate_tokens(str(system_msg.get("content", "")))
            system_budget = min(system_tokens, max(64, budget // 2))
            system_msg["content"] = self._truncate_text(str(system_msg.get("content", "")), system_budget, keep="start")
            remaining -= self._estimate_tokens(system_msg["content"])
            trimmed_messages.append(system_msg)

        for msg in reversed(messages):
            content = str(msg.get("content", ""))
            tokens = self._estimate_tokens(content)
            if tokens <= remaining:
                trimmed_messages.append(msg)
                remaining -= tokens
                continue
            if remaining <= 0:
                break
            msg = dict(msg)
            msg["content"] = self._truncate_text(content, remaining, keep="end")
            trimmed_messages.append(msg)
            break

        trimmed_messages_tail = list(reversed(trimmed_messages[1:] if system_msg else trimmed_messages))
        if system_msg:
            return [trimmed_messages[0]] + trimmed_messages_tail
        return trimmed_messages_tail

    def _handle_response(self, resp: Any, url: str) -> None:
        """Prüft die Response und wirft ggf. passende Exceptions."""
        if resp is None:
            raise TransientError(f"Verbindungsfehler oder Timeout zum LLM-Provider: {url}")

        # Falls es ein Response-Objekt ist (z.B. von requests)
        if hasattr(resp, "status_code"):
            if resp.status_code >= 400:
                msg = f"LLM-Provider Fehler ({resp.status_code}): {resp.text[:200]}"
                if _classify_status(resp.status_code) == "transient":
                    raise TransientError(msg, details={"status_code": resp.status_code})
                else:
                    raise PermanentError(msg, details={"status_code": resp.status_code})
