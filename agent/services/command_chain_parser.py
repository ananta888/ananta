from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandChainSegment:
    index: int
    raw: str
    argv: list[str]
    operator_before: str | None
    operator_after: str | None
    start: int
    end: int


@dataclass(frozen=True)
class CommandChainPlan:
    command: str
    segments: list[CommandChainSegment]
    unsupported_operators: list[str]
    allowed: bool
    denied_reason: str | None = None


class CommandChainParser:
    """Parse command chains into atomic segments without executing anything."""

    def parse(self, command: str) -> CommandChainPlan:
        text = str(command or "")
        if not text.strip():
            return CommandChainPlan(command=text, segments=[], unsupported_operators=[], allowed=True)

        segments: list[CommandChainSegment] = []
        unsupported: list[str] = []
        unsupported_seen: set[str] = set()
        in_single = False
        in_double = False
        escaped = False
        operator_before: str | None = None
        segment_start = 0
        buffer: list[str] = []
        index = 0

        while index < len(text):
            char = text[index]
            if escaped:
                buffer.append(char)
                escaped = False
                index += 1
                continue
            if char == "\\" and not in_single:
                buffer.append(char)
                escaped = True
                index += 1
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                buffer.append(char)
                index += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                buffer.append(char)
                index += 1
                continue
            if in_single or in_double:
                buffer.append(char)
                index += 1
                continue

            # Unsupported constructs by default.
            if text.startswith(">>", index) or text.startswith("<<", index):
                token = ">>" if text.startswith(">>", index) else "<<"
                if token not in unsupported_seen:
                    unsupported.append(token)
                    unsupported_seen.add(token)
                buffer.append(char)
                index += 1
                continue
            if char in {"|", ">", "<", "&", "`"}:
                # Keep && and || as supported operators.
                if char == "&" and text.startswith("&&", index):
                    pass
                elif char == "|" and text.startswith("||", index):
                    pass
                else:
                    if char not in unsupported_seen:
                        unsupported.append(char)
                        unsupported_seen.add(char)
            if char == "$":
                if text.startswith("$(", index):
                    if "$(" not in unsupported_seen:
                        unsupported.append("$(")
                        unsupported_seen.add("$(")
                elif text.startswith("${", index):
                    if "${" not in unsupported_seen:
                        unsupported.append("${")
                        unsupported_seen.add("${")

            if text.startswith("&&", index) or text.startswith("||", index) or char == ";":
                op = "&&" if text.startswith("&&", index) else ("||" if text.startswith("||", index) else ";")
                raw = "".join(buffer).strip()
                if not raw:
                    return CommandChainPlan(
                        command=text,
                        segments=[],
                        unsupported_operators=unsupported,
                        allowed=False,
                        denied_reason="empty_segment",
                    )
                operator_len = 2 if op in {"&&", "||"} else 1
                seg_end = index
                segments.append(
                    CommandChainSegment(
                        index=len(segments) + 1,
                        raw=raw,
                        argv=self._safe_argv(raw),
                        operator_before=operator_before,
                        operator_after=op,
                        start=segment_start,
                        end=seg_end,
                    )
                )
                buffer = []
                operator_before = op
                index += operator_len
                segment_start = index
                continue

            buffer.append(char)
            index += 1

        tail = "".join(buffer).strip()
        if not tail:
            if segments:
                return CommandChainPlan(
                    command=text,
                    segments=[],
                    unsupported_operators=unsupported,
                    allowed=False,
                    denied_reason="trailing_operator",
                )
            return CommandChainPlan(command=text, segments=[], unsupported_operators=unsupported, allowed=True)

        segments.append(
            CommandChainSegment(
                index=len(segments) + 1,
                raw=tail,
                argv=self._safe_argv(tail),
                operator_before=operator_before,
                operator_after=None,
                start=segment_start,
                end=len(text),
            )
        )
        if in_single or in_double or escaped:
            return CommandChainPlan(
                command=text,
                segments=[],
                unsupported_operators=unsupported,
                allowed=False,
                denied_reason="unbalanced_quotes_or_escape",
            )
        if unsupported:
            return CommandChainPlan(
                command=text,
                segments=segments,
                unsupported_operators=unsupported,
                allowed=False,
                denied_reason="unsupported_operator",
            )
        return CommandChainPlan(command=text, segments=segments, unsupported_operators=[], allowed=True)

    @staticmethod
    def _safe_argv(raw: str) -> list[str]:
        try:
            return shlex.split(raw)
        except Exception:
            return [raw]

