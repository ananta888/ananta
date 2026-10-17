"""Shared contract for the retrieval header ``setup_codecompass_index`` prepends.

Whole-file index records carry a ``# <path>`` line (and an optional
``# Themen: …`` line) in front of the raw file text so retrieval can surface the
path and semantic tags. That annotation is *not* part of the source file:

- the passage projector strips it so callers see real file lines;
- the semantic graph parser strips it so language adapters parse source text
  rather than treating ``#`` as a syntax error (Java, C, JSON, …).

Both consumers share this one implementation so the header format has a single
owner instead of drifting copies.
"""

from __future__ import annotations

from typing import Any


def strip_index_header(content: str, path: Any) -> tuple[str, int]:
    """Return ``(source, header_lines)`` for a possibly header-prefixed record.

    ``path`` is the record's repository-relative path, which the header echoes
    as ``# <path>``. Records without that exact leading line are returned
    unchanged with ``header_lines == 0``.
    """

    lines = content.split("\n")
    if not path or not lines or lines[0].strip() != f"# {path}":
        return content, 0
    header = 2 if len(lines) > 1 and lines[1].startswith("# Themen: ") else 1
    return "\n".join(lines[header:]), header
