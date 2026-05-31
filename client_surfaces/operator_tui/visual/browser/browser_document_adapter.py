from __future__ import annotations

import html
import re
from pathlib import Path

from client_surfaces.operator_tui.visual.browser.center_content_snapshot import CenterContentSnapshot

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "center_document.html"

# Tags that must be stripped from html_preview content (security)
_UNSAFE_TAG_RE = re.compile(
    r"<\s*(script|iframe|object|embed|applet|base|form|input|button|textarea|select|meta)"
    r"[^>]*>.*?</\s*\1\s*>|<\s*(script|iframe|object|embed|applet|base)[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_JAVASCRIPT_HREF_RE = re.compile(r'href\s*=\s*["\']?\s*javascript:', re.IGNORECASE)
_ON_HANDLER_RE = re.compile(r'\bon\w+\s*=\s*["\'][^"\']*["\']', re.IGNORECASE)


def _load_template() -> str:
    if _TEMPLATE_PATH.exists():
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    # Minimal fallback if template file is missing
    return (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
        "<title>{{TITLE}}</title></head><body>{{BODY}}</body></html>"
    )


def _escape_plain_text(text: str) -> str:
    """Escape plain text so it cannot be interpreted as HTML."""
    return html.escape(text, quote=True)


def _plain_to_html(text: str, *, title: str = "") -> str:
    escaped = _escape_plain_text(text)
    heading = f"<h1>{html.escape(title)}</h1>\n" if title else ""
    return f"{heading}<pre>{escaped}</pre>"


def _markdown_to_html(source: str, *, title: str = "") -> str:
    """Convert Markdown source to HTML body using a minimal built-in converter.

    No external dependencies required.  Handles headings, bold/italic, code
    fences, inline code, blockquotes, lists, and horizontal rules.
    """
    lines = source.splitlines()
    out: list[str] = []
    if title:
        out.append(f"<h1>{html.escape(title)}</h1>")

    in_code_fence = False
    fence_lang = ""
    fence_lines: list[str] = []
    in_ul = False
    in_ol = False

    def _close_list() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def _inline(text: str) -> str:
        """Apply inline Markdown formatting."""
        # Inline code — escape content
        def _code_sub(m: re.Match[str]) -> str:
            return f"<code>{html.escape(m.group(1))}</code>"
        text = re.sub(r"`([^`]+)`", _code_sub, text)
        # Bold+italic
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        # Bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # Italic
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        # Links
        def _link_sub(m: re.Match[str]) -> str:
            label = html.escape(m.group(1))
            href = html.escape(m.group(2))
            if href.lower().startswith("javascript:"):
                return label
            return f'<a href="{href}">{label}</a>'
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link_sub, text)
        return text

    for raw_line in lines:
        # Code fences
        if raw_line.startswith("```"):
            if not in_code_fence:
                _close_list()
                in_code_fence = True
                fence_lang = raw_line[3:].strip()
                fence_lines = []
            else:
                in_code_fence = False
                body = html.escape("\n".join(fence_lines))
                if fence_lang == "mermaid":
                    out.append(f'<div class="mermaid-source">{body}</div>')
                else:
                    lang_cls = f' class="language-{html.escape(fence_lang)}"' if fence_lang else ""
                    out.append(f"<pre><code{lang_cls}>{body}</code></pre>")
                fence_lang = ""
                fence_lines = []
            continue
        if in_code_fence:
            fence_lines.append(raw_line)
            continue

        line = raw_line.rstrip()

        # Headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            _close_list()
            level = len(m.group(1))
            text = html.escape(m.group(2).strip())
            out.append(f"<h{level}>{text}</h{level}>")
            continue

        # Horizontal rule
        if re.match(r"^(\*{3,}|-{3,}|_{3,})\s*$", line):
            _close_list()
            out.append("<hr>")
            continue

        # Blockquote
        if line.startswith("> "):
            _close_list()
            content = _inline(html.escape(line[2:]))
            out.append(f"<blockquote>{content}</blockquote>")
            continue

        # Unordered list
        m_ul = re.match(r"^[\*\-\+]\s+(.*)", line)
        if m_ul:
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            content = _inline(html.escape(m_ul.group(1)))
            out.append(f"<li>{content}</li>")
            continue

        # Ordered list
        m_ol = re.match(r"^\d+\.\s+(.*)", line)
        if m_ol:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            content = _inline(html.escape(m_ol.group(1)))
            out.append(f"<li>{content}</li>")
            continue

        # Blank line
        if not line.strip():
            _close_list()
            out.append("")
            continue

        # Regular paragraph line
        _close_list()
        content = _inline(html.escape(line))
        out.append(f"<p>{content}</p>")

    _close_list()
    if in_code_fence and fence_lines:
        # Unclosed fence — still render
        body = html.escape("\n".join(fence_lines))
        out.append(f"<pre><code>{body}</code></pre>")

    return "\n".join(out)


def _sanitize_html(raw_html: str) -> str:
    """Sanitize html_preview content: strip unsafe tags and attributes."""
    sanitized = _UNSAFE_TAG_RE.sub("", raw_html)
    sanitized = _JAVASCRIPT_HREF_RE.sub('href="#"', sanitized)
    sanitized = _ON_HANDLER_RE.sub("", sanitized)
    return sanitized


class BrowserDocumentAdapter:
    """Convert a CenterContentSnapshot into a complete, safe local HTML document.

    Design goals:
    - Plain text is always HTML-escaped (no script injection possible).
    - Markdown is converted with a minimal stdlib-only converter.
    - html_preview content is sanitized before inclusion.
    - No external CDN or network requests in generated documents.
    - Output is deterministic for the same input.
    """

    def __init__(self, *, template_override: str = "") -> None:
        self._template = template_override if template_override else _load_template()

    def to_html(self, snapshot: CenterContentSnapshot) -> str:
        """Convert snapshot to a complete HTML document string."""
        title = html.escape(snapshot.title or "Ananta Center View")
        body = self._build_body(snapshot)
        doc = self._template.replace("{{TITLE}}", title).replace("{{BODY}}", body)
        return doc

    def _build_body(self, snapshot: CenterContentSnapshot) -> str:
        ct = snapshot.content_type
        title = snapshot.title or ""

        if snapshot.unsupported_reason:
            msg = html.escape(snapshot.unsupported_reason)
            return f"<p><em>Browser preview unavailable: {msg}</em></p>"

        if ct == "html_preview":
            if snapshot.html_text:
                return _sanitize_html(snapshot.html_text)
            return _plain_to_html(snapshot.source_text, title=title)

        if ct in {"markdown", "mermaid_markdown"}:
            return _markdown_to_html(snapshot.source_text, title=title)

        if ct in {"plain_text", "ansi_text", "source_code", "artifact_preview"}:
            return _plain_to_html(snapshot.source_text, title=title)

        # Unknown type — escape and display
        return _plain_to_html(snapshot.source_text, title=title)
