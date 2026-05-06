from __future__ import annotations

import re


WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


def extract_wiki_semantic_signals(raw_text: str) -> dict[str, list[str]]:
    text = str(raw_text or "")
    links: list[str] = []
    categories: list[str] = []
    for raw_match in WIKI_LINK_PATTERN.findall(text):
        value = str(raw_match or "").strip()
        if not value:
            continue
        target = value.split("|", 1)[0].strip()
        if not target:
            continue
        if ":" in target:
            prefix, rest = target.split(":", 1)
            if prefix.strip().lower() == "kategorie" and rest.strip():
                categories.append(rest.strip())
                continue
        links.append(target)
    return {
        "links": sorted(dict.fromkeys(links)),
        "categories": sorted(dict.fromkeys(categories)),
    }

