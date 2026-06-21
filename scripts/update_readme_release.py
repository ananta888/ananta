from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
START_MARKER = "<!-- ANANTA_RELEASES_START -->"
END_MARKER = "<!-- ANANTA_RELEASES_END -->"
DEFAULT_REPO = "ananta888/ananta"


def github_request(path: str, token: str | None) -> dict[str, Any]:
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    url = f"https://api.github.com/repos/{repo}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ananta-readme-release-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed: {exc.code} {detail}") from exc


def release_from_event(event_path: str | None) -> dict[str, Any] | None:
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding="utf-8"))
    release = event.get("release")
    if isinstance(release, dict):
        return release
    return None


def latest_release(token: str | None) -> dict[str, Any] | None:
    release = release_from_event(os.environ.get("GITHUB_EVENT_PATH"))
    if release:
        return release
    try:
        return github_request("/releases/latest", token)
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise


def render_block(release: dict[str, Any] | None) -> str:
    if not release:
        latest_line = "- **Latest release:** noch kein GitHub Release veroeffentlicht"
    else:
        tag = release.get("tag_name") or "unknown"
        name = release.get("name") or tag
        url = release.get("html_url") or f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', DEFAULT_REPO)}/releases/tag/{tag}"
        prerelease = " beta/pre-release" if release.get("prerelease") else ""
        latest_line = f"- **Latest release:** [{name}]({url}) (`{tag}`{prerelease})"
    return "\n".join(
        [
            START_MARKER,
            "## Releases",
            "",
            latest_line,
            "- **Stable release:** noch nicht verfuegbar",
            "",
            END_MARKER,
        ]
    )


def update_readme(block: str) -> bool:
    text = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        re.DOTALL,
    )
    if pattern.search(text):
        new_text = pattern.sub(block, text)
    else:
        insertion = block + "\n\n"
        badge_block = re.search(r"^(# Ananta\n\n(?:\[!\[.*?\]\(.*?\)\]\(.*?\)\n)+)\n?", text, re.MULTILINE)
        if badge_block:
            pos = badge_block.end()
            new_text = text[:pos] + "\n" + insertion + text[pos:]
        else:
            new_text = insertion + text
    if new_text == text:
        return False
    README_PATH.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Update README release references.")
    parser.add_argument("--check", action="store_true", help="Fail when README would change.")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    release = latest_release(token)
    block = render_block(release)
    changed = update_readme(block)
    if args.check and changed:
        print("README.md release block was outdated and has been updated locally.")
        return 1
    print("README.md release block updated." if changed else "README.md release block already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
