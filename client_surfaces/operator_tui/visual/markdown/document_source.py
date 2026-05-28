from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentSource:
    kind: str  # "inline" | "artifact" | "file"
    content_or_ref: str
    title: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"inline", "artifact", "file"}:
            raise ValueError(f"unknown source kind {self.kind!r}")

    @property
    def identity_hash(self) -> str:
        return hashlib.sha256(f"{self.kind}:{self.content_or_ref}".encode()).hexdigest()[:16]


def inline_source(text: str, *, title: str = "") -> DocumentSource:
    return DocumentSource(kind="inline", content_or_ref=text, title=title)


def artifact_source(artifact_id: str, *, title: str = "") -> DocumentSource:
    return DocumentSource(kind="artifact", content_or_ref=artifact_id, title=title)


def file_source(path: str, *, title: str = "") -> DocumentSource:
    return DocumentSource(kind="file", content_or_ref=path, title=title)


def resolve_source(
    source: DocumentSource,
    *,
    allowed_roots: tuple[str, ...] = (),
    artifacts: dict[str, str] | None = None,
) -> tuple[str, str | None]:
    """Return (text_content, error_reason). error_reason is None on success."""
    if source.kind == "inline":
        return source.content_or_ref, None

    if source.kind == "artifact":
        store = artifacts or {}
        text = store.get(source.content_or_ref)
        if text is None:
            return "", f"artifact {source.content_or_ref!r} not found"
        return text, None

    if source.kind == "file":
        path = Path(source.content_or_ref).expanduser().resolve()
        roots = (
            [Path(r).expanduser().resolve() for r in allowed_roots]
            if allowed_roots
            else [Path.cwd().resolve()]
        )
        if not any(_is_within(path, root) for root in roots):
            return "", f"file {path} outside allowed roots"
        try:
            return path.read_text(encoding="utf-8"), None
        except OSError as exc:
            return "", f"cannot read {path}: {exc}"

    return "", f"unknown source kind {source.kind!r}"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
