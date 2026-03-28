from __future__ import annotations

from typing import Protocol


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
