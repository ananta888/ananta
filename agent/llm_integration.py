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
            resp = _http_post(urls["ollama"], {"model": model, "prompt": full_prompt}, timeout=timeout)
            if isinstance(resp, dict):
                return resp.get("response", "")
            return resp if isinstance(resp, str) else ""
        
        elif provider == "lmstudio":
            resp = _http_post(urls["lmstudio"], {"model": model, "prompt": full_prompt}, timeout=timeout)
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
            
            resp = _http_post(
                urls["openai"],
                {
                    "model": model or "gpt-4o-mini",
                    "messages": messages,
                },
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
        
        else:
            logging.error(f"Unbekannter Provider: {provider}")
            return ""
