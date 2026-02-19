from __future__ import annotations

import subprocess
import sys


def tracked_markdown_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "*.md"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    failures: list[str] = []
    for md_path in tracked_markdown_files():
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            failures.append(f"{md_path}: not valid UTF-8")
            continue
        if "\ufffd" in text:
            failures.append(f"{md_path}: contains replacement character (possible mojibake)")
    if failures:
        for line in failures:
            print(line)
        return 1
    print("markdown encoding check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
