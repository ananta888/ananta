from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping


class ParserGuardViolation(ValueError):
    diagnostic_code = "parser_limit_exceeded"

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code

    def as_diagnostic(self, *, path: str) -> dict[str, object]:
        return {
            "severity": "warning",
            "code": self.diagnostic_code,
            "reason_code": self.reason_code,
            "message": str(self),
            "path": path,
            "line": None,
        }


class ParserTimeoutViolation(ParserGuardViolation):
    diagnostic_code = "parser_timeout"


class ParserSecurityViolation(ParserGuardViolation):
    diagnostic_code = "security_blocked"


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_file_bytes: int = 1_048_576
    max_lines: int = 50_000
    parser_timeout_ms: int = 2_000
    max_output_records: int = 5_000
    max_xml_nodes: int = 20_000
    max_xml_depth: int = 64
    max_yaml_aliases: int = 50
    max_notebook_cells: int = 2_000
    max_notebook_cell_chars: int = 100_000
    max_notebook_output_bytes: int = 0
    max_csv_rows: int = 10_000
    max_csv_columns: int = 256

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items() if hasattr(self, "__dict__") else (
            (field, getattr(self, field)) for field in self.__slots__
        ):
            if int(value) < 0 or (name != "max_notebook_output_bytes" and int(value) == 0):
                raise ValueError(f"invalid_parser_limit:{name}")

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "ParserLimits":
        source = env if env is not None else os.environ
        defaults = cls()
        values = {}
        for field in cls.__dataclass_fields__:
            env_name = f"ANANTA_CODECOMPASS_{field.upper()}"
            raw = source.get(env_name)
            if raw is None or not str(raw).strip():
                values[field] = getattr(defaults, field)
                continue
            try:
                values[field] = int(str(raw).strip())
            except ValueError as exc:
                raise ValueError(f"invalid_parser_limit:{field}") from exc
        return cls(**values)

    def preflight(self, *, path: str, content: str) -> None:
        normalized = str(path or "").replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if not normalized or parsed.is_absolute() or ".." in parsed.parts:
            raise ParserSecurityViolation(
                "path_traversal",
                "Parser input path must be repository-relative and traversal-free.",
            )
        if "\0" in content:
            raise ParserSecurityViolation("binary_input", "NUL-containing input is not parsed as source text.")
        byte_size = len(content.encode("utf-8", errors="replace"))
        if byte_size > self.max_file_bytes:
            raise ParserGuardViolation(
                "file_size_limit",
                f"Input has {byte_size} bytes; limit is {self.max_file_bytes}.",
            )
        line_count = content.count("\n") + 1
        if line_count > self.max_lines:
            raise ParserGuardViolation(
                "line_limit",
                f"Input has {line_count} lines; limit is {self.max_lines}.",
            )

    def budget(self) -> "ParserBudget":
        return ParserBudget(
            deadline=time.monotonic() + (self.parser_timeout_ms / 1000.0),
            max_output_records=self.max_output_records,
        )


@dataclass(frozen=True, slots=True)
class ParserBudget:
    deadline: float
    max_output_records: int

    def check_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise ParserTimeoutViolation("wall_time_limit", "Parser wall-time budget was exceeded.")

    def check_record_count(self, count: int) -> None:
        if int(count) > self.max_output_records:
            raise ParserGuardViolation(
                "output_record_limit",
                f"Parser emitted {count} records; limit is {self.max_output_records}.",
            )


_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(?P<prefix>\s*[^\n:=]*(?:password|passwd|token|api[_-]?key|private[_-]?key|secret)"
    r"[^\n:=]*\s*[:=]\s*)(?P<value>[^\n]+)$"
)


def redact_secret_values(content: str) -> tuple[str, int]:
    redacted, count = _SECRET_ASSIGNMENT.subn(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        str(content),
    )
    return redacted, count
