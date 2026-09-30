"""Bounded SQL DDL lexing primitives used by :class:`SqlExtractor`.

The lexer splits SQL text into statements (respecting quotes, dollar quotes
and comments) and offers the small parenthesis/identifier helpers the DDL
outline parser needs. It never executes SQL.
"""

from __future__ import annotations

import re

SQL_IDENTIFIER_PART = r'(?:(?:"(?:""|[^"])+")|(?:`[^`]+`)|(?:\[[^\]]+\])|(?:[A-Za-z_][\w$]*))'
SQL_IDENTIFIER = rf"{SQL_IDENTIFIER_PART}(?:\.{SQL_IDENTIFIER_PART})*"


class SqlDdlLexer:
    """Stateless tokenizer helpers for SQL DDL outlines."""

    IDENTIFIER_PART = SQL_IDENTIFIER_PART
    IDENTIFIER = SQL_IDENTIFIER

    @staticmethod
    def split_statements(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        statements: list[tuple[str, int]] = []
        diagnostics: list[tuple[str, int]] = []
        start = 0
        i = 0
        quote: str | None = None
        dollar_tag: str | None = None
        block_comment = False
        line_comment = False
        while i < len(text):
            char = text[i]
            next_char = text[i + 1] if i + 1 < len(text) else ""
            if line_comment:
                if char == "\n":
                    line_comment = False
                i += 1
                continue
            if block_comment:
                if char == "*" and next_char == "/":
                    block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if dollar_tag is not None:
                if text.startswith(dollar_tag, i):
                    i += len(dollar_tag)
                    dollar_tag = None
                else:
                    i += 1
                continue
            if quote is not None:
                if char == quote:
                    if next_char == quote:
                        i += 2
                        continue
                    quote = None
                elif char == "\\" and quote in {"'", '"'}:
                    i += 2
                    continue
                i += 1
                continue
            if char == "-" and next_char == "-":
                line_comment = True
                i += 2
                continue
            if char == "/" and next_char == "*":
                block_comment = True
                i += 2
                continue
            if char in {"'", '"', "`"}:
                quote = char
                i += 1
                continue
            if char == "$":
                tag_match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[i:])
                if tag_match:
                    dollar_tag = tag_match.group(0)
                    i += len(dollar_tag)
                    continue
            if char == ";":
                statements.append((text[start:i], start))
                start = i + 1
            i += 1
        if text[start:].strip():
            statements.append((text[start:], start))
        if quote is not None:
            diagnostics.append(("sql_unterminated_quote", max(start, len(text) - 1)))
        if dollar_tag is not None:
            diagnostics.append(("sql_unterminated_dollar_quote", max(start, len(text) - 1)))
        if block_comment:
            diagnostics.append(("sql_unterminated_comment", max(start, len(text) - 1)))
        return statements, diagnostics

    @staticmethod
    def without_leading_comments(value: str) -> str:
        return re.sub(r"\A(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", "", value, flags=re.DOTALL)

    @staticmethod
    def parenthesized_body(text: str, opening: int) -> str | None:
        depth = 0
        quote: str | None = None
        for index in range(opening, len(text)):
            char = text[index]
            if quote:
                if char == quote and (index + 1 >= len(text) or text[index + 1] != quote):
                    quote = None
                continue
            if char in {"'", '"', "`"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return text[opening + 1 : index]
        return None

    @staticmethod
    def split_top_level(text: str) -> list[str]:
        parts: list[str] = []
        start = 0
        depth = 0
        quote: str | None = None
        for index, char in enumerate(text):
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in {"'", '"', "`"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                parts.append(text[start:index])
                start = index + 1
        parts.append(text[start:])
        return parts

    @staticmethod
    def clean_identifier(value: str) -> str:
        return ".".join(part.strip('"`[]').replace('""', '"') for part in value.split("."))
