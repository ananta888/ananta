from __future__ import annotations


def render_markdown_lines(source: str, *, width: int = 80, max_lines: int = 80) -> list[str]:
    lines: list[str] = []
    in_code = False
    for raw_line in str(source or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            lines.append("CODE" if in_code else "END")
            continue
        if in_code:
            lines.append("  " + _clip(line, width - 2))
        elif stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            lines.append(_clip(f"{'#' * min(level, 3)} {title}", width))
        elif stripped.startswith(("- ", "* ")):
            lines.append(_clip("- " + stripped[2:].strip(), width))
        elif stripped.startswith(">"):
            lines.append(_clip("| " + stripped[1:].strip(), width))
        elif "|" in stripped and stripped.count("|") >= 2:
            lines.append(_clip("TABLE " + stripped.strip("| "), width))
        else:
            lines.append(_clip(stripped, width))
        if len(lines) >= max_lines:
            lines.append("...")
            break
    return lines or ["empty markdown"]


def _clip(value: str, width: int) -> str:
    text = str(value)
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."
