from __future__ import annotations

import re


def clean_wiki_markup(raw_text: str) -> str:
    text = str(raw_text or "")
    if not text:
        return ""
    text = re.sub(r"\{\{[^{}]{0,4000}\}\}", " ", text)
    text = re.sub(r"\[\[Kategorie:[^\]]+\]\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[Datei:[^\]]+\]\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"<ref[^>/]*>.*?</ref>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"==+\s*([^=\n]+?)\s*==+", r" \1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

