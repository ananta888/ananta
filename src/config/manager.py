from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field
import yaml


class ConfigSchema(BaseModel):
    """Pydantic representation of the controller/agent configuration."""

    active_agent: str = "default"
    controller_url: str = "http://localhost:8081"
    agents: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    api_endpoints: List[Dict[str, Any]] = Field(default_factory=list)
    prompt_templates: Dict[str, Any] = Field(default_factory=dict)
    log_paths: Dict[str, str] = Field(default_factory=dict)


class ConfigManager:
    """Load and persist configuration from a YAML file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> ConfigSchema:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        else:
            data = {}
        return ConfigSchema(**data)

    def save(self, cfg: ConfigSchema) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg.dict(), fh, sort_keys=False)

    # Convenience methods mirroring old interface
    def read(self) -> Dict[str, Any]:
        return self.load().dict()

    def write(self, data: Dict[str, Any]) -> None:
        self.save(ConfigSchema(**data))
