"""Small telemetry port shared by CodeCompass parser pipelines."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

FileTypeTelemetryPort = Callable[..., None]


def observe_file_type_parser_result(**values: Any) -> None:
    """Default lazy adapter; telemetry must never change parser outcomes."""

    try:
        from agent.services.file_type_metrics_service import get_file_type_metrics_service

        get_file_type_metrics_service().observe_path_result(**values)
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "CodeCompass file-type telemetry unavailable: %s",
            exc,
        )


def emit_file_type_telemetry(
    observer: FileTypeTelemetryPort,
    *,
    pipeline: str,
    path: str,
    outcome: str,
    duration_seconds: float,
    byte_size: int,
    symbol_count: int,
    edge_count: int,
    fallback_reason: str | None = None,
    diagnostics: Sequence[str] = (),
) -> None:
    try:
        observer(
            pipeline=pipeline,
            path=path,
            outcome=outcome,
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            symbol_count=symbol_count,
            edge_count=edge_count,
            fallback_reason=fallback_reason,
            diagnostics=tuple(diagnostics),
        )
    except Exception:
        logging.getLogger(__name__).debug(
            "Injected CodeCompass file-type telemetry port failed.",
            exc_info=True,
        )


__all__ = [
    "FileTypeTelemetryPort",
    "emit_file_type_telemetry",
    "observe_file_type_parser_result",
]
