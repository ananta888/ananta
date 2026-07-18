"""Hub-owned file-type activation policy for rag-helper indexing.

The neutral registry describes capabilities, while this adapter projects the
configured Hub rollout onto rag-helper dispatch keys and individual paths.
It deliberately contains no indexing or persistence logic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from ananta_contracts import (
    FileTypeClassification,
    FileTypeClassifier,
    FileTypeDescriptor,
    FileTypeRolloutPolicy,
    FileTypeSupportRegistry,
    file_type_descriptor_hashes,
)


class RagHelperFileTypePolicyError(ValueError):
    """Raised when registry declarations cannot be dispatched by rag-helper."""


class RagHelperFileTypePolicy:
    """Fail-closed projection of canonical format policy onto rag-helper."""

    PIPELINE = "rag_helper"

    def __init__(
        self,
        *,
        registry: FileTypeSupportRegistry,
        rollout: FileTypeRolloutPolicy,
        runtime_dispatch_keys: Iterable[str],
        dispatch_key_resolver: Callable[[Path], str],
    ) -> None:
        self.registry = registry
        self.rollout = rollout
        self._classifier = FileTypeClassifier(registry)
        self._runtime_dispatch_keys = frozenset(
            str(key).strip().lower() for key in runtime_dispatch_keys
        )
        self._dispatch_key_resolver = dispatch_key_resolver

    def dispatch_keys(self) -> frozenset[str]:
        """Return verified runtime keys for every activated rag-helper type."""

        keys: set[str] = set()
        missing_selectors: list[str] = []
        for descriptor in self.registry.descriptors:
            if not self._allows_descriptor(descriptor):
                continue
            descriptor_keys = self._descriptor_dispatch_keys(descriptor)
            if not descriptor_keys:
                missing_selectors.append(descriptor.format_id)
            keys.update(descriptor_keys)

        unsupported_keys = sorted(keys - self._runtime_dispatch_keys)
        if missing_selectors or unsupported_keys:
            details: list[str] = []
            if missing_selectors:
                details.append(f"missing_selector_dispatch={','.join(sorted(missing_selectors))}")
            if unsupported_keys:
                details.append(f"missing_runtime_dispatch={','.join(unsupported_keys)}")
            raise RagHelperFileTypePolicyError(
                "rag_helper_file_type_registry_drift:" + ";".join(details)
            )
        if not keys:
            raise RagHelperFileTypePolicyError("rag_helper_no_enabled_file_types")
        return frozenset(sorted(keys))

    def classify_file(self, path: Path, *, relative_path: str) -> FileTypeClassification | None:
        """Classify a file using bounded inert content sniffing for shebangs."""

        return self._classifier.classify(
            relative_path,
            first_line=(
                self._bounded_first_line(path)
                if not Path(relative_path).suffix
                else None
            ),
            is_text=False,
        )

    def allows_file(self, path: Path, relative_path: str) -> bool:
        """Return whether the Hub rollout permits this exact classified path."""

        classification = self.classify_file(path, relative_path=relative_path)
        return classification is not None and self._allows_descriptor(classification.descriptor)

    def descriptor_dispatch_keys(self, descriptor: FileTypeDescriptor) -> frozenset[str]:
        """Expose checked dispatch keys for one allowed descriptor."""

        if not self._allows_descriptor(descriptor):
            return frozenset()
        keys = self._descriptor_dispatch_keys(descriptor)
        unsupported = sorted(keys - self._runtime_dispatch_keys)
        if not keys or unsupported:
            detail = ",".join(unsupported) or descriptor.format_id
            raise RagHelperFileTypePolicyError(
                f"rag_helper_file_type_registry_drift:{detail}"
            )
        return frozenset(sorted(keys))

    def as_dict(self) -> dict[str, object]:
        return {
            "registry_version": self.registry.registry_version,
            "registry_digest": self.registry.digest,
            "pipeline": self.PIPELINE,
            "rollout": self.rollout.as_dict(),
            "dispatch_keys": sorted(self.dispatch_keys()),
            "descriptor_hashes": file_type_descriptor_hashes(self.registry),
            "enabled_pipeline_format_ids": [
                descriptor.format_id
                for descriptor in self.registry.descriptors
                if self._allows_descriptor(descriptor)
            ],
        }

    def effective_format_ids(self, dispatch_keys: Iterable[str]) -> frozenset[str]:
        """Return allowed formats reachable through the narrowed runtime keys."""

        active_keys = {str(key).strip().lower() for key in dispatch_keys}
        return frozenset(
            descriptor.format_id
            for descriptor in self.registry.descriptors
            if self._allows_descriptor(descriptor)
            and bool(self._descriptor_dispatch_keys(descriptor) & active_keys)
        )

    def _allows_descriptor(self, descriptor: FileTypeDescriptor) -> bool:
        return (
            self.rollout.allows(descriptor)
            and descriptor.support_for(self.PIPELINE).indexed.configured
        )

    def _descriptor_dispatch_keys(self, descriptor: FileTypeDescriptor) -> set[str]:
        selectors = descriptor.selectors
        keys = {
            selector.removeprefix(".").lower()
            for selector in selectors.extensions
            if selector.removeprefix(".")
        }
        synthetic_names = (
            *selectors.exact_filenames,
            *selectors.filename_patterns,
            *(f"file{suffix}" for suffix in selectors.compound_suffixes),
        )
        for selector in synthetic_names:
            synthetic = selector.replace("*", "sample")
            key = self._dispatch_key_resolver(Path(synthetic)).strip().lower()
            if key:
                keys.add(key)
        return keys

    @staticmethod
    def _bounded_first_line(path: Path) -> str | None:
        try:
            if path.is_symlink() or not path.is_file():
                return None
            with path.open("rb") as handle:
                return handle.readline(512).decode("utf-8", errors="strict").strip()
        except (OSError, UnicodeDecodeError):
            return None


__all__ = ["RagHelperFileTypePolicy", "RagHelperFileTypePolicyError"]
