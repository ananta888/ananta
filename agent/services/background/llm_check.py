import logging
import threading
import time
from agent.config import settings

def _sleep_with_shutdown(total_seconds: int) -> None:
    import agent.common.context
    for _ in range(total_seconds):
        if agent.common.context.shutdown_requested:
            break
        time.sleep(1)

def _get_llm_target(app) -> tuple[str, str] | None:
    provider = str((app.config.get("AGENT_CONFIG", {}) or {}).get("default_provider") or settings.default_provider or "")
    url = app.config["PROVIDER_URLS"].get(provider)
    if not url or provider in ["openai", "anthropic"]:
        logging.info(f"LLM-Check fuer {provider} uebersprungen (Cloud-Provider oder keine URL).")
        return None
    return provider, url

def _handle_llm_probe_result(provider: str, url: str, latency: float, is_ok: bool, last_state_ok: bool | None) -> bool:
    if is_ok:
        if latency > 2.0:
            logging.warning(f"LLM-Latenz-Warnung: {provider} antwortet langsam ({latency:.2f}s).")
            from agent.llm_integration import _report_llm_failure
            _report_llm_failure(provider)
        if last_state_ok is not True:
            logging.info(f"LLM-Verbindung zu {provider} ist ERREICHBAR. (Latenz: {latency:.2f}s)")
        return True
    if last_state_ok is not False:
        logging.warning(f"!!! LLM-WARNUNG !!!: {provider} unter {url} ist aktuell NICHT ERREICHBAR.")
        logging.warning("Tipp: Fuehren Sie 'setup_host_services.ps1' auf Ihrem Windows-Host aus.")
    return False

def _run_llm_check_loop(app) -> None:
    import agent.common.context
    from agent.common.http import HttpClient

    time.sleep(5)
    target = _get_llm_target(app)
    if target is None:
        return
    provider, url = target
    check_client = HttpClient(timeout=5, retries=0)
    logging.info(f"LLM-Monitoring fuer {provider} ({url}) gestartet.")
    last_state_ok = None

    while not agent.common.context.shutdown_requested:
        try:
            start_time = time.time()
            res = check_client.get(url, timeout=5, silent=True, return_response=True)
            latency = time.time() - start_time
            is_ok = res is not None and res.status_code < 500
            last_state_ok = _handle_llm_probe_result(provider, url, latency, is_ok, last_state_ok)
        except Exception as e:
            if last_state_ok is not False:
                logging.warning(f"Fehler beim Test der LLM-Verbindung: {e}")
            last_state_ok = False
        _sleep_with_shutdown(300)
    logging.info("LLM-Monitoring-Task beendet.")

def start_llm_check_thread(app):
    """Prueft periodisch die Erreichbarkeit des konfigurierten LLM-Providers."""
    t = threading.Thread(target=lambda: _run_llm_check_loop(app), daemon=True)
    import agent.common.context
    agent.common.context.active_threads.append(t)
    t.start()
