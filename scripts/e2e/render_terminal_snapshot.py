from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    from scripts.e2e.e2e_artifacts import redact_sensitive_text
except ModuleNotFoundError:
    import sys

    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from scripts.e2e.e2e_artifacts import redact_sensitive_text


def render_terminal_snapshot(content: str, *, width: int = 100) -> str:
    cleaned = redact_sensitive_text(str(content))
    cleaned = re.sub(r"\s+$", "", cleaned, flags=re.MULTILINE)
    lines = []
    for raw_line in cleaned.splitlines():
        line = raw_line.replace("\t", "    ")
        if len(line) <= width:
            lines.append(line)
            continue
        lines.append(line[:width])
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize terminal output into deterministic snapshot text.")
    parser.add_argument("source", help="Input text file path.")
    parser.add_argument("--out", default="", help="Optional output path. Default prints to stdout.")
    parser.add_argument("--width", type=int, default=100)
    args = parser.parse_args()

    source = Path(args.source)
    snapshot = render_terminal_snapshot(source.read_text(encoding="utf-8"), width=args.width)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(snapshot, encoding="utf-8")
        return 0
    print(snapshot, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
