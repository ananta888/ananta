import logging
import time
from agent.metrics import LLM_CALL_DURATION, RETRIES_TOTAL
from agent.utils import _http_post
from agent.config import settings

HTTP_TIMEOUT = 120

def _call_llm(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT, history: list | None = None) -> str:
    """Wrapper für _execute_llm_call mit automatischer Retry-Logik."""
    max_retries = getattr(settings, "retry_count", 3)
    backoff_factor = getattr(settings, "retry_backoff", 1.5)

    for attempt in range(max_retries + 1):
        if attempt > 0:
            logging.info(f"LLM Retry Versuch {attempt}/{max_retries} für Provider {provider}")
            RETRIES_TOTAL.inc()
            time.sleep(backoff_factor ** attempt)

        res = _execute_llm_call(
            provider=provider,
            model=model,
            prompt=prompt,
            urls=urls,
            api_key=api_key,
            timeout=timeout,
            history=history
        )
        
        if res and res.strip():
            return res
        
        logging.warning(f"LLM Aufruf lieferte kein Ergebnis (Versuch {attempt + 1}/{max_retries + 1})")

    logging.error(f"LLM Aufruf nach {max_retries} Retries endgültig fehlgeschlagen.")
    return ""

def _execute_llm_call(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT, history: list | None = None) -> str:
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
            
            # System-Prompt Extraktion
            system_prompt = "Du bist ein hilfreicher KI-Assistent."
            user_content = prompt
            
            # Falls der Prompt mit einer System-Anweisung beginnt, extrahieren wir sie
            if prompt.startswith("Du bist") or prompt.startswith("You are") or "System:" in prompt[:100]:
                if "\n\n" in prompt:
                    parts = prompt.split("\n\n", 1)
                    system_prompt = parts[0]
                    user_content = parts[1]
            
            messages = []
            if history:
                for h in history:
                    messages.append({"role": "user", "content": h.get("prompt") or "Vorheriger Schritt"})
                    assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                    messages.append({"role": "assistant", "content": assistant_msg})
                    if "output" in h:
                        # Anthropic Best-Practice: Feedback als User-Message
                        messages.append({"role": "user", "content": f"Befehlsausgabe: {h.get('output')}"})
            
            messages.append({"role": "user", "content": user_content})
            
            # JSON Erzwingung mittels Pre-fill (optional)
            is_json = "json" in prompt.lower()
            if is_json:
                messages.append({"role": "assistant", "content": "{"})
            
            payload = {
                "model": model or "claude-3-5-sonnet-20240620",
                "max_tokens": 4096,
                "messages": messages,
                "system": system_prompt
            }
            
            resp = _http_post(
                urls["anthropic"],
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

        else:
            logging.error(f"Unbekannter Provider: {provider}")
            return ""
