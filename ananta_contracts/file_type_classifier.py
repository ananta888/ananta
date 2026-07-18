"""Pure deterministic classification for the file-type support contract."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from .file_type_support import FileTypeDescriptor, FileTypeSupportRegistry


class FileTypeMatchKind(str, Enum):
    EXACT_FILENAME = "exact_filename"
    FILENAME_PATTERN = "filename_pattern"
    COMPOUND_SUFFIX = "compound_suffix"
    EXTENSION = "extension"
    SHEBANG = "shebang"
    TEXT_FALLBACK = "text_fallback"


@dataclass(frozen=True, slots=True)
class FileTypeClassification:
    path: str
    format_id: str
    match_kind: FileTypeMatchKind
    matched_selector: str
    descriptor: FileTypeDescriptor

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "format_id": self.format_id,
            "match_kind": self.match_kind.value,
            "matched_selector": self.matched_selector,
        }


class FileTypeClassifier:
    """Classify metadata without reading files or granting access to them.

    Precedence is fixed by control flow: exact filename, filename pattern,
    compound suffix, extension, shebang, then explicit text fallback.  Within
    one class, explicit ``match_priority``, selector specificity, and format id
    provide a stable ordering.
    """

    def __init__(self, registry: FileTypeSupportRegistry) -> None:
        self.registry = registry
        self._enabled = tuple(item for item in registry.descriptors if item.enabled)

    def classify(
        self,
        path: str,
        *,
        first_line: str | None = None,
        is_text: bool = False,
    ) -> FileTypeClassification | None:
        normalized_path = _normalize_path(path)
        basename = PurePosixPath(normalized_path).name
        basename_lower = basename.lower()

        exact = [
            (descriptor, selector)
            for descriptor in self._enabled
            for selector in descriptor.selectors.exact_filenames
            if basename == selector
        ]
        selected = self._select(normalized_path, FileTypeMatchKind.EXACT_FILENAME, exact)
        if selected is not None:
            return selected

        patterns = [
            (descriptor, pattern)
            for descriptor in self._enabled
            for pattern in descriptor.selectors.filename_patterns
            if fnmatch.fnmatchcase(normalized_path if "/" in pattern else basename, pattern)
        ]
        selected = self._select(normalized_path, FileTypeMatchKind.FILENAME_PATTERN, patterns)
        if selected is not None:
            return selected

        compound = [
            (descriptor, suffix)
            for descriptor in self._enabled
            for suffix in descriptor.selectors.compound_suffixes
            if basename_lower.endswith(suffix)
        ]
        selected = self._select(normalized_path, FileTypeMatchKind.COMPOUND_SUFFIX, compound)
        if selected is not None:
            return selected

        extension = PurePosixPath(basename_lower).suffix
        extensions = [
            (descriptor, selector)
            for descriptor in self._enabled
            for selector in descriptor.selectors.extensions
            if extension == selector
        ]
        selected = self._select(normalized_path, FileTypeMatchKind.EXTENSION, extensions)
        if selected is not None:
            return selected

        bounded_first_line = str(first_line or "")[:512]
        if bounded_first_line.startswith("#!"):
            shebangs = [
                (descriptor, pattern)
                for descriptor in self._enabled
                for pattern in descriptor.selectors.shebang_patterns
                if re.search(pattern, bounded_first_line) is not None
            ]
            selected = self._select(normalized_path, FileTypeMatchKind.SHEBANG, shebangs)
            if selected is not None:
                return selected

        if is_text:
            fallbacks = [
                (descriptor, "*")
                for descriptor in self._enabled
                if descriptor.selectors.text_fallback
            ]
            return self._select(normalized_path, FileTypeMatchKind.TEXT_FALLBACK, fallbacks)
        return None

    @staticmethod
    def _select(
        path: str,
        match_kind: FileTypeMatchKind,
        candidates: list[tuple[FileTypeDescriptor, str]],
    ) -> FileTypeClassification | None:
        if not candidates:
            return None
        descriptor, selector = sorted(
            candidates,
            key=lambda item: (
                -(item[0].match_priority if item[0].match_priority is not None else 0),
                -len(item[1]),
                item[0].format_id,
                item[1],
            ),
        )[0]
        return FileTypeClassification(
            path=path,
            format_id=descriptor.format_id,
            match_kind=match_kind,
            matched_selector=selector,
            descriptor=descriptor,
        )


def _normalize_path(path: str) -> str:
    value = str(path or "").replace("\\", "/")[:4096]
    while value.startswith("./"):
        value = value[2:]
    return value or "."
