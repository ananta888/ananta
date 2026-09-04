"""Structured, fail-closed parsing of bounded CrossHair counterexamples."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

_CALL_START = re.compile(r"(?P<symbol>[A-Za-z_][A-Za-z0-9_.]*)\(")


class CrossHairOutputParseError(ValueError):
    """Raised when a counterexample marker cannot be decoded safely."""


@dataclass(frozen=True, slots=True)
class ParsedCrossHairCounterexample:
    symbol: str
    arguments: dict[str, Any]
    message: str


class CrossHairOutputParser:
    """Parse literal call arguments without evaluating CrossHair output."""

    def parse(self, output: str) -> tuple[ParsedCrossHairCounterexample, ...]:
        results: list[ParsedCrossHairCounterexample] = []
        for line in output.splitlines():
            marker = " when calling "
            if marker not in line:
                continue
            prefix, call_text = line.split(marker, 1)
            match = _CALL_START.match(call_text.strip())
            if match is None:
                raise CrossHairOutputParseError("crosshair_counterexample_call_invalid")
            arguments_text = self._balanced_arguments(call_text.strip(), match.end() - 1)
            arguments = self._literal_arguments(arguments_text)
            message = prefix.rsplit(": ", 1)[-1].strip()[:500] or "contract violation"
            results.append(
                ParsedCrossHairCounterexample(
                    symbol=match.group("symbol"),
                    arguments=arguments,
                    message=message,
                )
            )
        return tuple(results)

    @staticmethod
    def _balanced_arguments(call_text: str, opening_index: int) -> str:
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(opening_index, len(call_text)):
            character = call_text[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {"'", '"'}:
                quote = character
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
                if depth == 0:
                    return call_text[opening_index + 1 : index]
                if depth < 0:
                    break
        raise CrossHairOutputParseError("crosshair_counterexample_parentheses_invalid")

    @staticmethod
    def _literal_arguments(arguments_text: str) -> dict[str, Any]:
        try:
            expression = ast.parse(f"_target({arguments_text})", mode="eval").body
        except SyntaxError as exc:
            raise CrossHairOutputParseError("crosshair_counterexample_arguments_invalid") from exc
        if not isinstance(expression, ast.Call) or expression.keywords is None:
            raise CrossHairOutputParseError("crosshair_counterexample_arguments_invalid")
        if any(isinstance(argument, ast.Starred) for argument in expression.args):
            raise CrossHairOutputParseError("crosshair_counterexample_star_args_denied")
        positional = [CrossHairOutputParser._literal(argument) for argument in expression.args]
        keywords: dict[str, Any] = {}
        for keyword in expression.keywords:
            if keyword.arg is None or keyword.arg in keywords:
                raise CrossHairOutputParseError("crosshair_counterexample_keyword_invalid")
            keywords[keyword.arg] = CrossHairOutputParser._literal(keyword.value)
        if positional and keywords:
            return {"args": positional, "kwargs": keywords}
        if positional:
            return {"args": positional}
        return keywords

    @staticmethod
    def _literal(node: ast.AST) -> Any:
        try:
            value = ast.literal_eval(node)
            return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise CrossHairOutputParseError("crosshair_counterexample_literal_unsupported") from exc


__all__ = [
    "CrossHairOutputParseError",
    "CrossHairOutputParser",
    "ParsedCrossHairCounterexample",
]
