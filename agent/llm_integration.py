import logging
import time
from urllib.parse import urlsplit
from collections import defaultdict
from agent.metrics import LLM_CALL_DURATION, RETRIES_TOTAL
from agent.utils import _http_post, _http_get, log_llm_entry
from agent.config import settings
from typing import Optional
from flask import has_request_context, g, request

HTTP_TIMEOUT = 120

# Circuit Breaker Status
CIRCUIT_BREAKER = {
    "failures": defaultdict(int),
    "last_failure": defaultdict(float),
    "open": defaultdict(bool)
}
CB_THRESHOLD = 5
CB_RECOVERY_TIME = 60 # Sekunden

def _check_circuit_breaker(provider: str) -> bool:
    """Prüft ob der Circuit Breaker für einen Provider offen ist."""
    if CIRCUIT_BREAKER["open"][provider]:
        if time.time() - CIRCUIT_BREAKER["last_failure"][provider] > CB_RECOVERY_TIME:
            logging.info(f"Circuit Breaker für {provider} wechselt in Halboffen-Zustand.")
            CIRCUIT_BREAKER["open"][provider] = False
            CIRCUIT_BREAKER["failures"][provider] = 0
            return True
        return False
    return True

def _report_llm_failure(provider: str):
    """Registriert einen Fehler für den Circuit Breaker."""
    CIRCUIT_BREAKER["failures"][provider] += 1
    CIRCUIT_BREAKER["last_failure"][provider] = time.time()
    if CIRCUIT_BREAKER["failures"][provider] >= CB_THRESHOLD:
        if not CIRCUIT_BREAKER["open"][provider]:
            logging.error(f"CIRCUIT BREAKER GEÖFFNET für Provider {provider}. Pausiere Aufrufe für {CB_RECOVERY_TIME}s.")
            CIRCUIT_BREAKER["open"][provider] = True

def _report_llm_success(provider: str):
    """Registriert einen Erfolg für den Circuit Breaker."""
    CIRCUIT_BREAKER["failures"][provider] = 0
    CIRCUIT_BREAKER["open"][provider] = False

def _build_chat_messages(prompt: str, history: list | None) -> list:
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

def _lmstudio_models_url(base_url: str) -> Optional[str]:
    if not base_url:
        return None
    if "/v1/" in base_url:
        parts = base_url.split("/v1/", 1)
        # Falls nach /v1/ noch etwas kommt (z.B. completions), schneiden wir es ab
        return parts[0].rstrip("/") + "/v1/models"
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    # Sicherstellen, dass wir /v1/models anhängen, falls es fehlte
    return f"{parsed.scheme}://{parsed.netloc}/v1/models"

def _resolve_lmstudio_model(model: Optional[str], base_url: str, timeout: int) -> Optional[str]:
    if model:
        return model
    models_url = _lmstudio_models_url(base_url)
    if not models_url:
        return None
    try:
        resp = _http_get(models_url, timeout=timeout, silent=True)
    except Exception:
        return None
        
    if isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, list) and data:
            # Filter embedding models (they fail if used as LLM in LM Studio)
            llm_candidates = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                mid = item.get("id") or item.get("name") or ""
                if "embed" in mid.lower():
                    continue
                llm_candidates.append(mid)
            
            if llm_candidates:
                return llm_candidates[0]
                
            # Fallback if no specific LLM found, take first available
            first = data[0]
            if isinstance(first, dict):
                return first.get("id") or first.get("name")
    return None

def generate_text(prompt: str, provider: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None, history: Optional[list] = None) -> str:
    """Höherwertige Funktion für LLM-Anfragen, nutzt Parameter oder Defaults."""
    p = provider or settings.default_provider
    m = model or settings.default_model
    
    urls = {
        "ollama": settings.ollama_url,
        "lmstudio": settings.lmstudio_url,
        "openai": settings.openai_url,
        "anthropic": settings.anthropic_url
    }
    
    if base_url:
        urls[p] = base_url
    
    key = api_key
    if not key:
        if p == "openai": key = settings.openai_api_key
        elif p == "anthropic": key = settings.anthropic_api_key

    return _call_llm(p, m, prompt, urls, key, history=history)

def _call_llm(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT, history: list | None = None) -> str:
    """Wrapper für _execute_llm_call mit automatischer Retry-Logik."""
    if not _check_circuit_breaker(provider):
        logging.warning(f"Abbruch: Circuit Breaker für {provider} ist offen.")
        return ""

    max_retries = getattr(settings, "retry_count", 3)
    backoff_factor = getattr(settings, "retry_backoff", 1.5)
    request_id = None
    request_path = None
    request_method = None
    if has_request_context():
        request_id = getattr(g, "llm_request_id", None)
        request_path = request.path
        request_method = request.method

    log_llm_entry(
        event="llm_call_start",
        request_id=request_id,
        provider=provider,
        model=model,
        prompt=prompt,
        history_len=len(history) if history else 0,
        request_path=request_path,
        request_method=request_method
    )

    for attempt in range(max_retries + 1):
        if attempt > 0:
            logging.info(f"LLM Retry Versuch {attempt}/{max_retries} für Provider {provider}")
            RETRIES_TOTAL.inc()
            time.sleep(backoff_factor ** attempt)

        try:
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
                _report_llm_success(provider)
                log_llm_entry(
                    event="llm_call_end",
                    request_id=request_id,
                    provider=provider,
                    model=model,
                    success=True,
                    attempts=attempt + 1,
                    response=res
                )
                return res
        except Exception as e:
            logging.warning(f"Fehler bei LLM-Aufruf (Versuch {attempt + 1}): {e}")
        
        logging.warning(f"LLM Aufruf lieferte kein Ergebnis oder schlug fehl (Versuch {attempt + 1}/{max_retries + 1})")

    _report_llm_failure(provider)
    logging.error(f"LLM Aufruf nach {max_retries} Retries endgültig fehlgeschlagen.")
    log_llm_entry(
        event="llm_call_end",
        request_id=request_id,
        provider=provider,
        model=model,
        success=False,
        attempts=max_retries + 1,
        response=""
    )
    return ""

def _execute_llm_call(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT, history: list | None = None) -> str:
    """Ruft den konfigurierten LLM-Provider auf und gibt den rohen Text zurück."""
    
    with LLM_CALL_DURATION.time():
        # Historie in den Prompt einbauen (für Ollama/LMStudio)
        full_prompt = prompt
        if history and provider != "openai":
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
            base_url = urls["lmstudio"]
            is_chat = "chat/completions" in (base_url or "").lower()
            lmstudio_model = _resolve_lmstudio_model(model, base_url, timeout)
            
            # Konstruiere die finale Anfrage-URL
            if is_chat:
                request_url = base_url
            elif "/v1" in base_url and not any(e in base_url for e in ["completions", "chat"]):
                request_url = base_url.rstrip("/") + "/completions"
            else:
                request_url = base_url

            max_tokens = 1024
            temperature = 0.2
            if is_chat:
                payload = {
                    "messages": _build_chat_messages(full_prompt, history),
                    "stream": False,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
                if lmstudio_model:
                    payload["model"] = lmstudio_model
            else:
                payload = {
                    "prompt": full_prompt,
                    "stream": False,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
                if lmstudio_model:
                    payload["model"] = lmstudio_model
            resp = _http_post(request_url, payload, timeout=timeout)
            if resp is None and is_chat:
                fallback_url = request_url.replace("/chat/completions", "/completions")
                fallback_payload = {
                    "prompt": full_prompt,
                    "stream": False,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
                if lmstudio_model:
                    fallback_payload["model"] = lmstudio_model
                logging.warning(f"LM Studio chat failed, retrying via completions: {fallback_url}")
                resp = _http_post(fallback_url, fallback_payload, timeout=timeout)
            if isinstance(resp, dict):
                if "response" in resp:
                    return resp.get("response", "")
                choices = resp.get("choices")
                if isinstance(choices, list) and choices:
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    if "text" in choice:
                        return choice.get("text", "")
                    message = choice.get("message")
                    if isinstance(message, dict):
                        return message.get("content", "")
                return ""
            return resp if isinstance(resp, str) else ""
        
        elif provider == "openai":
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
            messages = _build_chat_messages(prompt, history)
            
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
