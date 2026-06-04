import logging
import threading
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _classify_status(code: int) -> str:
    """Klassifiziert HTTP-Statuscodes in transient (retry sinnvoll) vs permanent."""
    if code in (408, 429) or 500 <= code < 600:
        return "transient"
    return "permanent"


def create_session(retries: int = 3, backoff_factor: float = 0.3, status_forcelist=(408, 429, 500, 502, 503, 504)):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class HttpClient:
    def __init__(self, timeout: int = 30, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.session = create_session(retries=retries)

    def head(self, url: str, timeout: Optional[int] = None) -> Any:
        try:
            r = self.session.head(url, timeout=timeout or self.timeout)
            r.raise_for_status()
            return True
        except requests.exceptions.RequestException:
            return False

    def get(
        self,
        url: str,
        params: dict | None = None,
        timeout: Optional[int] = None,
        return_response: bool = False,
        silent: bool = False,
        headers: dict | None = None,
    ) -> Any:
        try:
            request_kwargs: dict[str, Any] = {"params": params, "timeout": timeout or self.timeout}
            if headers:
                request_kwargs["headers"] = headers
            r = self.session.get(url, **request_kwargs)
            if return_response:
                return r
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout:
            if not silent:
                logging.warning(f"HTTP GET Timeout ({timeout or self.timeout}s): {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            # Fallback für host.docker.internal
            if "host.docker.internal" in url:
                from agent.utils import get_host_gateway_ip

                gateway = get_host_gateway_ip()
                if gateway:
                    fallback_url = url.replace("host.docker.internal", gateway)
                    if not silent:
                        logging.info(
                            f"host.docker.internal verweigert Verbindung. Versuche Fallback auf Gateway: {fallback_url}"
                        )
                    return self.get(
                        fallback_url, params=params, timeout=timeout, return_response=return_response, silent=silent, headers=headers
                    )

            if not silent:
                msg = f"HTTP GET Verbindungsfehler: {url} - {e}"
                # Tipp für lokale Verbindungen (host.docker.internal oder private IPs)
                if "host.docker.internal" in url or any(p in url for p in ["127.0.0.1", "192.168.", "172.", "10."]):
                    msg += (
                        " (Tipp: Stellen Sie sicher, dass der Dienst auf dem Host GESTARTET ist. "
                        "Nutzen Sie 'setup_host_services.ps1' für Firewall/Proxy-Konfiguration.)"
                    )
                logging.error(msg)
            return None
        except requests.exceptions.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code is not None:
                level = logging.warning if _classify_status(code) == "transient" else logging.error
                if not silent:
                    level(f"HTTP GET Fehler ({code}, {_classify_status(code)}): {url}")
            else:
                if not silent:
                    logging.error(f"HTTP GET Fehler: {url} - {e}")
            return None

    def post(
        self,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
        form: bool = False,
        timeout: Optional[int] = None,
        silent: bool = False,
        return_response: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        tracked_key = None
        tracked_session = None
        try:
            headers = (headers or {}).copy()
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key

            effective_timeout = timeout if timeout is not None and timeout > 0 else self.timeout
            request_session = self.session
            try:
                # Register per-request sessions so task-/goal-level cancellation can abort
                # all provider HTTP calls (e.g. Ollama via standard strategy).
                from agent.common.lmstudio_request_registry import _get_current_context, register_existing_session

                goal_id, task_id = _get_current_context()
                if goal_id or task_id:
                    # Use a plain session for tracked in-flight calls so close() can
                    # interrupt blocked requests similarly to the LMStudio path.
                    tracked_session = requests.Session()
                    tracked_key = register_existing_session(tracked_session)
                    request_session = tracked_session
                    request_box: dict[str, Any] = {"done": False, "response": None, "error": None}

                    def _do_request() -> None:
                        try:
                            if form:
                                request_box["response"] = request_session.post(
                                    url, data=data or {}, headers=headers, timeout=effective_timeout
                                )
                            else:
                                request_box["response"] = request_session.post(
                                    url, json=data or {}, headers=headers, timeout=effective_timeout
                                )
                        except Exception as exc:
                            request_box["error"] = exc
                        finally:
                            request_box["done"] = True

                    req_thread = threading.Thread(target=_do_request, daemon=True)
                    req_thread.start()
                    from agent.common.lmstudio_request_registry import is_cancelled

                    while not request_box["done"]:
                        if is_cancelled(goal_id, task_id):
                            try:
                                request_session.close()
                            except Exception:
                                pass
                            return None
                        req_thread.join(timeout=0.2)

                    if request_box["error"] is not None:
                        raise request_box["error"]
                    r = request_box["response"]
                    if return_response:
                        return r
                    r.raise_for_status()
                    try:
                        return r.json()
                    except ValueError:
                        return r.text
            except Exception:
                # Tracking is best-effort and must never break normal HTTP behavior.
                tracked_key = None
                tracked_session = None
                request_session = self.session

            if form:
                r = request_session.post(url, data=data or {}, headers=headers, timeout=effective_timeout)
            else:
                r = request_session.post(url, json=data or {}, headers=headers, timeout=effective_timeout)
            if return_response:
                return r
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout:
            if not silent:
                logging.warning(f"HTTP POST Timeout ({effective_timeout}s): {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            # Fallback für host.docker.internal
            if "host.docker.internal" in url:
                from agent.utils import get_host_gateway_ip

                gateway = get_host_gateway_ip()
                if gateway:
                    fallback_url = url.replace("host.docker.internal", gateway)
                    if not silent:
                        logging.info(
                            f"host.docker.internal verweigert Verbindung. Versuche Fallback auf Gateway: {fallback_url}"
                        )
                    return self.post(
                        fallback_url,
                        data=data,
                        headers=headers,
                        form=form,
                        timeout=timeout,
                        silent=silent,
                        idempotency_key=idempotency_key,
                    )

            if not silent:
                msg = f"HTTP POST Verbindungsfehler: {url} - {e}"
                # Tipp für lokale Verbindungen (host.docker.internal oder private IPs)
                if "host.docker.internal" in url or any(p in url for p in ["127.0.0.1", "192.168.", "172.", "10."]):
                    msg += (
                        " (Tipp: Stellen Sie sicher, dass der Dienst auf dem Host GESTARTET ist. "
                        "Nutzen Sie 'setup_host_services.ps1' für Firewall/Proxy-Konfiguration.)"
                    )
                logging.error(msg)
            return None
        except requests.exceptions.RequestException as e:
            if return_response and getattr(e, "response", None) is not None:
                return e.response
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code is not None:
                level = logging.warning if _classify_status(code) == "transient" else logging.error
                if not silent:
                    level(f"HTTP POST Fehler ({code}, {_classify_status(code)}): {url}")
            else:
                if not silent:
                    logging.error(f"HTTP POST Fehler: {url} - {e}")
            return None
        finally:
            if tracked_session is not None:
                try:
                    from agent.common.lmstudio_request_registry import release_session

                    release_session(tracked_key, tracked_session)
                except Exception:
                    pass


# Singleton-Instanz mit Standardwerten
_default_client = None


def get_default_client(timeout: int = 30, retries: int = 3):
    global _default_client
    if _default_client is None or _default_client.timeout != timeout:
        _default_client = HttpClient(timeout=timeout, retries=retries)
    return _default_client
