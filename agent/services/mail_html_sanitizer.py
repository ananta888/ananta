from __future__ import annotations

import html
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlsplit


_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
_VOID_TAGS = frozenset({"br", "hr"})
_DROP_CONTENT_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math", "form"})


class _SanitizerParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        clean_tag = tag.lower()
        if clean_tag in _DROP_CONTENT_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth or clean_tag not in _ALLOWED_TAGS:
            return
        clean_attrs: list[tuple[str, str]] = []
        for key, value in attrs:
            name = str(key).lower()
            raw = str(value or "")
            if name.startswith("on") or name in {"style", "src", "srcset", "background", "formaction"}:
                continue
            if clean_tag == "a" and name == "href" and _safe_href(raw):
                clean_attrs.append(("href", raw))
            elif clean_tag == "a" and name == "title":
                clean_attrs.append(("title", raw))
        rendered = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in clean_attrs)
        self.output.append(f"<{clean_tag}{rendered}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.lower()
        if clean_tag in _DROP_CONTENT_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if not self.drop_depth and clean_tag in _ALLOWED_TAGS and clean_tag not in _VOID_TAGS:
            self.output.append(f"</{clean_tag}>")

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            self.output.append(html.escape(data, quote=False))


def _safe_href(value: str) -> bool:
    clean = str(value or "").strip()
    if not clean:
        return False
    parsed = urlsplit(clean)
    return parsed.scheme.lower() in {"mailto", "cid"} and not parsed.username and not parsed.password


class MailHtmlSanitizer:
    def sanitize(self, value: str) -> str:
        parser = _SanitizerParser()
        parser.feed(str(value or ""))
        parser.close()
        return "".join(parser.output)


__all__ = ["MailHtmlSanitizer"]
