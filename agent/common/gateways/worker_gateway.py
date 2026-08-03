import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Optional

from agent.common.http import HttpClient
from agent.config import settings


class WorkerGateway(ABC):
    """Interface für die Kommunikation vom Hub zum Worker (DIP)."""

    @abstractmethod
    def forward_task(
        self,
        worker_url: str,
        endpoint: str,
        data: dict,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        pass


class HttpWorkerGateway(WorkerGateway):
    """HTTP-Implementierung des Worker-Gateways."""

    def __init__(self, timeout: Optional[int] = None, retries: Optional[int] = None):
        self.timeout = timeout or settings.http_timeout
        self.retries = retries or settings.retry_count
        self.client = HttpClient(timeout=self.timeout, retries=self.retries)

    def forward_task(
        self,
        worker_url: str,
        endpoint: str,
        data: dict,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            response = self.client.post(
                url,
                data=data,
                headers=headers,
                timeout=timeout,
                return_response=True,
                allow_redirects=False,
            )
            return self._response_payload(response)
        except Exception as exc:
            logging.error(
                "Fehler bei der Weiterleitung an Worker (%s): %s",
                url,
                exc,
            )
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def _response_payload(response: Any) -> Any:
        if response is None or isinstance(response, Mapping):
            return response
        status_code = int(
            getattr(response, "status_code", 500) or 500
        )
        close = getattr(response, "close", None)
        try:
            if 300 <= status_code < 400:
                return {
                    "status": "error",
                    "message": "worker_forward_redirect_forbidden",
                    "http_status": status_code,
                }
            if status_code >= 400:
                return {
                    "status": "error",
                    "message": "worker_forward_failed",
                    "http_status": status_code,
                }
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = getattr(response, "text", "")
            return body
        finally:
            if callable(close):
                close()

# Singleton-Instanz für den Hub
_default_worker_gateway = None


def get_worker_gateway() -> WorkerGateway:
    global _default_worker_gateway
    if _default_worker_gateway is None:
        _default_worker_gateway = HttpWorkerGateway()
    return _default_worker_gateway
