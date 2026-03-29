from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class HubGateway(Protocol):
    """Abstraktion für die Kommunikation mit dem Hub."""

    def register(self, agent_name: str, port: int, token: str, role: str = "worker", silent: bool = False) -> bool:
        """Registriert den Agenten beim Hub."""
        ...

    def approve_command(self, cmd: str, prompt: str) -> str | None:
        """Sendet einen Befehl zur Genehmigung an den Hub."""
        ...

    def log_event(self, event: str, **kwargs: Any) -> None:
        """Loggt ein Event an den Hub."""
        ...
