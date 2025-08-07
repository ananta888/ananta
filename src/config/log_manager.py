from __future__ import annotations

import logging.config
from pathlib import Path
from typing import Any, Dict

import yaml


class LogManager:
    """Configure Python logging using a YAML configuration file."""

    @staticmethod
    def setup(path: str | Path, **overrides: Any) -> None:
        cfg_path = Path(path)
        if not cfg_path.exists():
            return
        with cfg_path.open("r", encoding="utf-8") as fh:
            data: Dict[str, Any] = yaml.safe_load(fh) or {}
        # Allow overriding values such as log file locations
        for handler in data.get("handlers", {}).values():
            filename = overrides.get("filename")
            if filename and handler.get("class") == "logging.FileHandler":
                handler["filename"] = filename
        logging.config.dictConfig(data)
