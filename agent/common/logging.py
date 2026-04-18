import json
import logging
import logging.config
import os
import re
from contextvars import ContextVar
from typing import Optional

import yaml

from agent.common.redaction import redact, VisibilityLevel

# ContextVar für Korrelations-ID (Thread-sicher und Async-sicher)
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


class JsonFormatter(logging.Formatter):
    """Formatter für strukturiertes JSON-Logging mit Maskierung von Secrets."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()

        # Zentrale Redaction für die Nachricht
        msg = redact(msg)

        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
            "correlation_id": getattr(record, "correlation_id", correlation_id_ctx.get() or ""),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Füge extra Felder hinzu, falls vorhanden (ebenfalls maskiert)
        if hasattr(record, "extra_fields"):
            log_data.update(redact(record.extra_fields))

        return json.dumps(log_data, ensure_ascii=False)


def get_correlation_id() -> str:
    return correlation_id_ctx.get()


def set_correlation_id(cid: str):
    correlation_id_ctx.set(cid)


def setup_logging(
    level: str = "INFO", json_format: bool = False, log_file: Optional[str] = None, config_path: str = "log_config.yaml"
):
    """Konfiguriert das Logging-System."""

    # Factory für LogRecords anpassen, um correlation_id immer dabei zu haben
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.correlation_id = correlation_id_ctx.get()
        return record

    logging.setLogRecordFactory(record_factory)

    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                logging.config.dictConfig(config)
            logging.info(f"Logging initialized from {config_path}")
            return
        except Exception as e:
            print(f"Error loading logging config from {config_path}: {e}")

    # Fallback zur manuellen Konfiguration
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    # Bestehende Handler entfernen
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console Handler
    console_handler = logging.StreamHandler()
    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(name)s: %(message)s (cid: %(correlation_id)s)")
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File Handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.info(f"Logging initialized (level={level}, json={json_format}) - Fallback")
