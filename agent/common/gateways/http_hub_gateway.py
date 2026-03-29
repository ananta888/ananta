import logging
from typing import Any, Optional

from agent.common.http import HttpClient
from agent.config import settings


class HttpHubGateway:
    """Konkrete HTTP-Implementierung der Hub-Kommunikation."""

    def __init__(self, hub_url: Optional[str] = None):
        self.hub_url = hub_url or settings.hub_url
        self.client = HttpClient(timeout=settings.http_timeout, retries=settings.retry_count)

    def register(self, agent_name: str, port: int, token: str, role: str = "worker", silent: bool = False) -> bool:
        """Registriert den Agenten beim Hub via HTTP POST."""
        agent_url = settings.agent_url or f"http://localhost:{port}"
        payload = {"name": agent_name, "url": agent_url, "role": role, "token": token}
        try:
            response = self.client.post(f"{self.hub_url}/register", payload, silent=silent)
            if not silent:
                logging.info(f"Erfolgreich am Hub ({self.hub_url}) registriert.")

            # Token-Persistierung falls vom Hub zurückgegeben
            if isinstance(response, dict) and "agent_token" in response:
                new_token = response["agent_token"]
                if new_token and new_token != token:
                    settings.save_agent_token(new_token)
            return True
        except Exception as e:
            if not silent:
                logging.warning(f"Hub-Registrierung fehlgeschlagen: {e}")
            return False

    def approve_command(self, cmd: str, prompt: str) -> Optional[str]:
        """Sendet Befehl zur Genehmigung via HTTP POST. Gibt finalen Befehl oder None (SKIP) zurück."""
        try:
            approval = self.client.post(f"{self.hub_url}/approve", {"cmd": cmd, "summary": prompt}, form=True)
            if isinstance(approval, str):
                if approval.strip().upper() == "SKIP":
                    return None
                if approval.strip() not in ('{"status": "approved"}', "approved"):
                    return approval.strip()
            elif isinstance(approval, dict):
                override = approval.get("cmd")
                if isinstance(override, str) and override.strip():
                    return override.strip()
            return cmd
        except Exception as e:
            logging.error(f"Fehler bei der Genehmigungsanfrage am Hub: {e}")
            return cmd

    def log_event(self, event: str, **kwargs: Any) -> None:
        """Platzhalter für Event-Logging via HTTP."""
        pass
