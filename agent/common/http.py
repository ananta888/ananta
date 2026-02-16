import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from typing import Any, Optional


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
    ) -> Any:
        try:
            r = self.session.get(url, params=params, timeout=timeout or self.timeout)
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
                        fallback_url, params=params, timeout=timeout, return_response=return_response, silent=silent
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
        try:
            headers = (headers or {}).copy()
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key

            if form:
                r = self.session.post(url, data=data or {}, headers=headers, timeout=timeout or self.timeout)
            else:
                r = self.session.post(url, json=data or {}, headers=headers, timeout=timeout or self.timeout)
            if return_response:
                return r
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout:
            if not silent:
                logging.warning(f"HTTP POST Timeout ({timeout or self.timeout}s): {url}")
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


# Singleton-Instanz mit Standardwerten
_default_client = None


def get_default_client(timeout: int = 30, retries: int = 3):
    global _default_client
    if _default_client is None:
        _default_client = HttpClient(timeout=timeout, retries=retries)
    else:
        # Falls sich Parameter ändern, könnten wir sie hier theoretisch aktualisieren,
        # aber HttpClient.get/post erlauben ohnehin das Überschreiben des Timeouts.
        # Wir dokumentieren hier nur, dass der Singleton existiert.
        pass
    return _default_client
