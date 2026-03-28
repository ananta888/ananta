from __future__ import annotations

from typing import Protocol


class FileSkipped(Exception):
    def __init__(self, reason: str, details: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


class StructuredExtractor(Protocol):
    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        ...


class JavaLikeExtractor(Protocol):
    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        ...

    def parse(
        self,
        rel_path: str,
        text: str,
        known_package_types: dict[str, set[str]],
    ) -> tuple[list[dict], list[dict], list[dict], dict]:
        ...
