from __future__ import annotations

import re


SECTION_PATTERN = re.compile(r"(?m)^==+\s*([^=\n]+?)\s*==+\s*$")


def extract_wiki_sections(*, text: str, fallback_title: str = "Overview") -> list[dict[str, str]]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    matches = list(SECTION_PATTERN.finditer(normalized))
    if not matches:
        return [{"section_title": fallback_title, "content": normalized}]
    sections: list[dict[str, str]] = []
    prefix = normalized[: matches[0].start()].strip()
    if prefix:
        sections.append({"section_title": fallback_title, "content": prefix})
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        section_text = normalized[start:end].strip()
        if not section_text:
            continue
        section_title = str(match.group(1) or "").strip() or fallback_title
        sections.append({"section_title": section_title, "content": section_text})
    return sections or [{"section_title": fallback_title, "content": normalized}]

