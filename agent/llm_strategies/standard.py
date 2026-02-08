from typing import Optional, Any
from agent.llm_strategies.base import LLMStrategy
from agent.utils import _http_post

class OpenAIStrategy(LLMStrategy):
    def execute(
        self,
        model: str,
        prompt: str,
        url: str,
        api_key: Optional[str],
        history: Optional[list],
        timeout: int,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None
    ) -> Any:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        messages = self._build_chat_messages(prompt, history)
        
        payload = {
            "model": model or "gpt-4o-mini",
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        if "json" in prompt.lower() and not tools:
            payload["response_format"] = {"type": "json_object"}

        resp = _http_post(
            url,
            payload,
            headers=headers,
            timeout=timeout
        )
        if isinstance(resp, dict):
            try:
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content
            except (IndexError, AttributeError):
                return ""
        return resp if isinstance(resp, str) else ""

class AnthropicStrategy(LLMStrategy):
    def execute(
        self,
        model: str,
        prompt: str,
        url: str,
        api_key: Optional[str],
        history: Optional[list],
        timeout: int,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None
    ) -> Any:
        import logging
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        } if api_key else {}
        
        system_prompt = "Du bist ein hilfreicher KI-Assistent."
        user_content = prompt
        
        if prompt.startswith("Du bist") or prompt.startswith("You are") or "System:" in prompt[:100]:
            if "\n\n" in prompt:
                parts = prompt.split("\n\n", 1)
                system_prompt = parts[0]
                user_content = parts[1]
        
        messages = []
        if history:
            for h in history:
                if isinstance(h, dict) and "role" in h:
                    messages.append(h)
                else:
                    messages.append({"role": "user", "content": h.get("prompt") or "Vorheriger Schritt"})
                    assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                    messages.append({"role": "assistant", "content": assistant_msg})
                    if "output" in h:
                        messages.append({"role": "user", "content": f"Befehlsausgabe: {h.get('output')}"})
        
        messages.append({"role": "user", "content": user_content})
        
        is_json = "json" in prompt.lower() and not tools
        if is_json:
            messages.append({"role": "assistant", "content": "{"})
        
        payload = {
            "model": model or "claude-3-5-sonnet-20240620",
            "max_tokens": 4096,
            "messages": messages,
            "system": system_prompt
        }
        
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        resp = _http_post(
            url,
            payload,
            headers=headers,
            timeout=timeout
        )
        if isinstance(resp, dict):
            try:
                content = resp.get("content", [{}])[0].get("text", "")
                if is_json and not content.startswith("{"):
                    content = "{" + content
                return content
            except (IndexError, AttributeError):
                logging.error(f"Fehler beim Parsen der Anthropic-Antwort: {resp}")
                return ""
        return resp if isinstance(resp, str) else ""

class OllamaStrategy(LLMStrategy):
    def execute(
        self,
        model: str,
        prompt: str,
        url: str,
        api_key: Optional[str],
        history: Optional[list],
        timeout: int,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None
    ) -> Any:
        full_prompt = self._build_history_prompt(prompt, history)
        payload = {"model": model, "prompt": full_prompt, "stream": False}
        if tools:
            # Validierung und Bereinigung der Tools für Ollama
            valid_tools = []
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                
                # Ollama erwartet oft das OpenAI-Format: {"type": "function", "function": {...}}
                if tool.get("type") == "function" and "function" in tool:
                    f_data = tool["function"]
                    if "name" in f_data and "parameters" in f_data:
                        valid_tools.append(tool)
                    else:
                        import logging
                        logging.warning(f"OllamaStrategy: Tool-Funktion unvollständig: {f_data}")
                else:
                    import logging
                    logging.warning(f"OllamaStrategy: Tool-Format unbekannt oder ungültig: {tool}")
            
            if valid_tools:
                payload["tools"] = valid_tools
        
        if "json" in full_prompt.lower() and not tools:
            payload["format"] = "json"
        
        resp = _http_post(url, payload, timeout=timeout)
        if isinstance(resp, dict):
            return resp.get("response", "")
        return resp if isinstance(resp, str) else ""
