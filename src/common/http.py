
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from typing import Any, Optional

def create_session(retries: int = 3, backoff_factor: float = 0.3, status_forcelist=(500, 502, 504)):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

class HttpClient:
    def __init__(self, timeout: int = 30, retries: int = 3):
        self.timeout = timeout
        self.session = create_session(retries=retries)

    def get(self, url: str, params: dict | None = None, timeout: Optional[int] = None) -> Any:
        try:
            r = self.session.get(url, params=params, timeout=timeout or self.timeout)
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout:
            logging.warning(f"HTTP GET Timeout: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"HTTP GET Fehler: {url} - {e}")
            return None

    def post(self, url: str, data: dict | None = None, headers: dict | None = None, form: bool = False, timeout: Optional[int] = None) -> Any:
        try:
            if form:
                r = self.session.post(url, data=data or {}, headers=headers, timeout=timeout or self.timeout)
            else:
                r = self.session.post(url, json=data or {}, headers=headers, timeout=timeout or self.timeout)
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.exceptions.Timeout:
            logging.warning(f"HTTP POST Timeout: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"HTTP POST Fehler: {url} - {e}")
            return None

# Singleton-Instanz mit Standardwerten (wird in ai_agent.py genutzt)
_default_client = None

def get_default_client(timeout: int = 30, retries: int = 3):
    global _default_client
    if _default_client is None:
        _default_client = HttpClient(timeout=timeout, retries=retries)
    return _default_client
