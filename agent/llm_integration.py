import logging
from agent.metrics import LLM_CALL_DURATION
from agent.utils import _http_post

HTTP_TIMEOUT = 120

def _call_llm(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT, history: list | None = None) -> str:
    """Ruft den konfigurierten LLM-Provider auf und gibt den rohen Text zurück."""
    
    with LLM_CALL_DURATION.time():
        # Historie in den Prompt einbauen (für Ollama/LMStudio)
        full_prompt = prompt
        if history and provider != "openai":
            history_str = "\n\nHistorie bisheriger Aktionen:\n"
            for h in history:
                history_str += f"- Prompt: {h.get('prompt')}\n"
                history_str += f"  Reasoning: {h.get('reason')}\n"
                history_str += f"  Befehl: {h.get('command')}\n"
                if "output" in h:
                    out = h.get('output', '')
                    if len(out) > 500: out = out[:500] + "..."
                    history_str += f"  Ergebnis: {out}\n"
            full_prompt = history_str + "\nAktueller Auftrag:\n" + prompt

        if provider == "ollama":
            payload = {"model": model, "prompt": full_prompt, "stream": False}
            # Versuche JSON-Modus zu erzwingen, falls gewünscht (hier als Standard für Robustheit)
            if "json" in full_prompt.lower():
                payload["format"] = "json"
            
            resp = _http_post(urls["ollama"], payload, timeout=timeout)
            if isinstance(resp, dict):
                return resp.get("response", "")
            return resp if isinstance(resp, str) else ""
        
        elif provider == "lmstudio":
            payload = {"model": model, "prompt": full_prompt, "stream": False}
            resp = _http_post(urls["lmstudio"], payload, timeout=timeout)
            if isinstance(resp, dict):
                return resp.get("response", "")
            return resp if isinstance(resp, str) else ""
        
        elif provider == "openai":
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
            messages = []
            if history:
                for h in history:
                    messages.append({"role": "user", "content": h.get("prompt") or ""})
                    assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                    messages.append({"role": "assistant", "content": assistant_msg})
                    if "output" in h:
                        messages.append({"role": "system", "content": f"Befehlsausgabe: {h.get('output')}"})
            
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": model or "gpt-4o-mini",
                "messages": messages,
            }
            if "json" in full_prompt.lower():
                payload["response_format"] = {"type": "json_object"}

            resp = _http_post(
                urls["openai"],
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
        
        elif provider == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            } if api_key else {}
            
            messages = []
            if history:
                for h in history:
                    messages.append({"role": "user", "content": h.get("prompt") or ""})
                    assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                    messages.append({"role": "assistant", "content": assistant_msg})
                    if "output" in h:
                        # Anthropic erlaubt kein 'system' in messages (nur top-level)
                        # Wir packen die Ausgabe in die nächste User-Message oder als Assistant-Klarstellung
                        messages.append({"role": "user", "content": f"Befehlsausgabe: {h.get('output')}"})
            
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": model or "claude-3-5-sonnet-20240620",
                "max_tokens": 4096,
                "messages": messages,
            }
            
            resp = _http_post(
                urls["anthropic"],
                payload,
                headers=headers,
                timeout=timeout
            )
            if isinstance(resp, dict):
                try:
                    # Anthropic Format: resp['content'][0]['text']
                    content = resp.get("content", [{}])[0].get("text", "")
                    return content
                except (IndexError, AttributeError):
                    logging.error(f"Fehler beim Parsen der Anthropic-Antwort: {resp}")
                    return ""
            return resp if isinstance(resp, str) else ""

        else:
            logging.error(f"Unbekannter Provider: {provider}")
            return ""
