from __future__ import annotations

import logging

from agent.bootstrap.request_hooks import configure_audit_logger
from agent.config import settings


def test_configure_audit_logger_reuses_existing_file_handler(
    monkeypatch,
    tmp_path,
) -> None:
    audit_path = tmp_path / "audit.log"
    existing_handler = logging.FileHandler(audit_path, encoding="utf-8")
    audit_logger = logging.getLogger("audit")
    original_handlers = list(audit_logger.handlers)
    original_level = audit_logger.level
    original_propagate = audit_logger.propagate
    audit_logger.handlers = [existing_handler]
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "log_json", True)

    try:
        configure_audit_logger()
        configure_audit_logger()

        matching_handlers = [
            handler
            for handler in audit_logger.handlers
            if isinstance(handler, logging.FileHandler)
            and handler.baseFilename == str(audit_path.resolve())
        ]
        assert matching_handlers == [existing_handler]
        assert audit_logger.level == logging.INFO
        assert audit_logger.propagate is False
    finally:
        audit_logger.handlers = original_handlers
        audit_logger.setLevel(original_level)
        audit_logger.propagate = original_propagate
        existing_handler.close()
