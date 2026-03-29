import logging
from abc import ABC, abstractmethod
from typing import Any, Optional
from agent.common.http import HttpClient
from agent.config import settings

class WorkerGateway(ABC):
    """Interface für die Kommunikation vom Hub zum Worker (DIP)."""

    @abstractmethod
    def forward_task(self, worker_url: str, endpoint: str, data: dict, token: Optional[str] = None) -> Any:
        pass

class HttpWorkerGateway(WorkerGateway):
    """HTTP-Implementierung des Worker-Gateways."""

    def __init__(self, timeout: Optional[int] = None, retries: Optional[int] = None):
        self.timeout = timeout or settings.http_timeout
        self.retries = retries or settings.retry_count
        self.client = HttpClient(timeout=self.timeout, retries=self.retries)

    def forward_task(self, worker_url: str, endpoint: str, data: dict, token: Optional[str] = None) -> Any:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            return self.client.post(url, data=data, headers=headers)
        except Exception as e:
            logging.error(f"Fehler bei der Weiterleitung an Worker ({url}): {e}")
            return {"status": "error", "message": str(e)}

# Singleton-Instanz für den Hub
_default_worker_gateway = None

def get_worker_gateway() -> WorkerGateway:
    global _default_worker_gateway
    if _default_worker_gateway is None:
        _default_worker_gateway = HttpWorkerGateway()
    return _default_worker_gateway
