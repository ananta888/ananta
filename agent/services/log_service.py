from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BoundLogService:
    service: "LogService"
    logger_name: str

    def debug(self, message: str, *args: Any, extra_fields: dict[str, Any] | None = None) -> None:
        self.service.debug(message, *args, logger_name=self.logger_name, extra_fields=extra_fields)

    def info(self, message: str, *args: Any, extra_fields: dict[str, Any] | None = None) -> None:
        self.service.info(message, *args, logger_name=self.logger_name, extra_fields=extra_fields)

    def warning(self, message: str, *args: Any, extra_fields: dict[str, Any] | None = None) -> None:
        self.service.warning(message, *args, logger_name=self.logger_name, extra_fields=extra_fields)

    def error(self, message: str, *args: Any, extra_fields: dict[str, Any] | None = None) -> None:
        self.service.error(message, *args, logger_name=self.logger_name, extra_fields=extra_fields)

    def critical(self, message: str, *args: Any, extra_fields: dict[str, Any] | None = None) -> None:
        self.service.critical(message, *args, logger_name=self.logger_name, extra_fields=extra_fields)

    def exception(self, message: str, *args: Any, extra_fields: dict[str, Any] | None = None) -> None:
        self.service.exception(message, *args, logger_name=self.logger_name, extra_fields=extra_fields)


class LogService:
    def __init__(self, default_logger_name: str = "agent") -> None:
        self._default_logger_name = str(default_logger_name or "agent").strip() or "agent"

    def bind(self, logger_name: str | None) -> BoundLogService:
        return BoundLogService(self, self._normalize_logger_name(logger_name))

    def debug(
        self,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self._emit(logging.DEBUG, message, *args, logger_name=logger_name, extra_fields=extra_fields)

    def info(
        self,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self._emit(logging.INFO, message, *args, logger_name=logger_name, extra_fields=extra_fields)

    def warning(
        self,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self._emit(logging.WARNING, message, *args, logger_name=logger_name, extra_fields=extra_fields)

    def error(
        self,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self._emit(logging.ERROR, message, *args, logger_name=logger_name, extra_fields=extra_fields)

    def critical(
        self,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self._emit(logging.CRITICAL, message, *args, logger_name=logger_name, extra_fields=extra_fields)

    def exception(
        self,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self._emit(logging.ERROR, message, *args, logger_name=logger_name, extra_fields=extra_fields, exc_info=True)

    def _emit(
        self,
        level: int,
        message: str,
        *args: Any,
        logger_name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
        exc_info: bool | BaseException | tuple[Any, Any, Any] | None = None,
    ) -> None:
        logger = logging.getLogger(self._normalize_logger_name(logger_name))
        payload = dict(extra_fields or {})
        logger.log(level, message, *args, extra={"extra_fields": payload} if payload else None, exc_info=exc_info)

    def _normalize_logger_name(self, logger_name: str | None) -> str:
        value = str(logger_name or "").strip()
        return value or self._default_logger_name


log_service = LogService()


def get_log_service() -> LogService:
    return log_service
