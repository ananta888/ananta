from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET_READMES = [
    "README.md",
    "architektur/README.md",
    "src/README.md",
    "agent/README.md",
    "frontend-angular/README.md",
    "docs/taiga/README.md",
]
IGNORED_PREFIXES = ("test-results/", "dist/", "build/")

MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
CODE_PATH_RE = re.compile(r"`([^`\n]+)`")


def _is_external(ref: str) -> bool:
    r = ref.strip().lower()
    return r.startswith(("http://", "https://", "mailto:", "#"))


def _normalize_path_token(token: str) -> str | None:
    t = token.strip()
    if not t or " " in t:
        return None
    if t.startswith("/") and "." not in t:
        return None
    if t.startswith(IGNORED_PREFIXES):
        return None
    if t.startswith(("./", "../", "/")):
        return t
    if "/" in t and "." in t:
        return t
    return None


def _resolve(base_file: Path, ref: str, from_code: bool) -> Path | None:
    raw = ref.strip()
    if _is_external(raw):
        return None
    path_part = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not path_part:
        return None
    if path_part.startswith("/") and "." not in path_part:
        return None
    if path_part.startswith("/"):
        return ROOT / path_part.lstrip("/")
    if from_code and not path_part.startswith(("./", "../")):
        return ROOT / path_part
    return (base_file.parent / path_part).resolve()


def _collect_refs(text: str) -> set[tuple[str, bool]]:
    refs: set[tuple[str, bool]] = set()
    for m in MD_LINK_RE.finditer(text):
        refs.add((m.group(1), False))
    for m in CODE_PATH_RE.finditer(text):
        maybe_path = _normalize_path_token(m.group(1))
        if maybe_path:
            refs.add((maybe_path, True))
    return refs


def main() -> int:
    failures: list[str] = []
    for rel in TARGET_READMES:
        path = ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: file missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for ref, from_code in sorted(_collect_refs(text)):
            resolved = _resolve(path, ref, from_code)
            if resolved is None:
                continue
            if not resolved.exists():
                failures.append(f"{rel}: missing reference `{ref}`")
    if failures:
        for line in failures:
            print(line)
        return 1
    print("markdown link check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
