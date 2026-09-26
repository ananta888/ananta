#!/usr/bin/env python3
"""Sync one commit into the Hub's incremental CodeCompass layers.

Reads every tracked text file of the commit tree (not the working tree),
redacts secrets, sends the snapshot manifest, uploads only the contents the
Hub does not have yet and asks the Hub to index the commit. The Hub plans
the delta and delegates the build to a Worker; this client never builds.

A per-checkout cache (``.git/codecompass-layer-sync-cache.json``) remembers
the redacted hash of each git blob, so a commit re-reads only changed files.

Usage (normally from the post-commit hook):
    python scripts/codecompass_layer_sync.py [--commit HEAD] [--profile ananta-project]
Auth: ANANTA_HUB_TOKEN, or INITIAL_ADMIN_USER / INITIAL_ADMIN_PASSWORD from the
environment or the checkout's .env. Hub: ANANTA_HUB_URL (default http://localhost:5000).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.codecompass_content_redaction import redact_sensitive_values  # noqa: E402

SNAPSHOT_SCHEMA = "codecompass.layer_snapshot.v1"
POLICY_VERSION = "layer-sync.v1"  # bump when redaction or selection changes: forces a new snapshot identity
MAX_TEXT_BYTES = 8 * 1024 * 1024
UPLOAD_BATCH_CHARS = 16 * 1024 * 1024
API = "/api/codecompass/layer-sync"


def git(*args: str, cwd: Path | None = None, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd or REPO, input=data, check=True, capture_output=True).stdout


def tree_blobs(commit: str) -> list[tuple[str, str]]:
    """``(blob_sha, path)`` of every regular file in the commit (no symlinks, no submodules)."""
    rows = []
    for entry in git("ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        if kind == "blob" and mode in ("100644", "100755"):
            rows.append((sha, path.decode("utf-8", "surrogateescape")))
    return rows


def read_blobs(shas: list[str]) -> Iterator[tuple[str, bytes]]:
    if not shas:
        return
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=REPO, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)
    assert process.stdin and process.stdout
    try:
        for sha in shas:
            process.stdin.write(sha.encode() + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            size = int(header[2])
            data = process.stdout.read(size)
            process.stdout.read(1)
            yield sha, data
    finally:
        process.stdin.close()
        process.wait()


def redacted_text(path: str, data: bytes) -> str | None:
    """The indexed text of a file, or ``None`` for binary, non-UTF-8 or oversized content."""
    if len(data) > MAX_TEXT_BYTES or b"\0" in data[:8192]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.strip():
        return None
    redacted, _changed = redact_sensitive_values(text, language="python" if path.endswith(".py") else None)
    return redacted


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BlobCache:
    """git blob sha -> [redacted sha256, size] or None (not indexed)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.entries: dict[str, Any] = payload["entries"] if payload.get("policy") == POLICY_VERSION else {}
        except (OSError, ValueError, KeyError):
            self.entries = {}

    def save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"policy": POLICY_VERSION, "entries": self.entries}), encoding="utf-8")
        tmp.replace(self._path)


def build_manifest(commit: str, cache: BlobCache) -> tuple[dict[str, Any], dict[str, tuple[str, str]]]:
    """The snapshot manifest plus ``redacted sha -> (blob sha, path)`` to re-read uploads."""
    blobs = tree_blobs(commit)
    unknown = sorted({sha for sha, _path in blobs if sha not in cache.entries})
    paths = {sha: path for sha, path in blobs}
    for sha, data in read_blobs(unknown):
        text = redacted_text(paths[sha], data)
        cache.entries[sha] = None if text is None else [sha256(text), len(text.encode("utf-8"))]
    files, sources = [], {}
    for sha, path in sorted(blobs, key=lambda item: item[1]):
        entry = cache.entries.get(sha)
        if entry is None:
            continue
        files.append({"path": path, "content_sha256": entry[0], "byte_size": entry[1], "outcome": "indexed"})
        sources.setdefault(entry[0], (sha, path))
    identity = json.dumps([POLICY_VERSION, [[item["path"], item["content_sha256"]] for item in files]],
                          separators=(",", ":"))
    manifest = {"schema": SNAPSHOT_SCHEMA, "snapshot_revision": sha256(identity), "commit_sha": commit,
                "policy": POLICY_VERSION, "files": files}
    return manifest, sources


class Hub:
    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token

    def post(self, path: str, body: dict[str, Any], *, timeout: float = 300) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url + path, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read(2000).decode("utf-8", "replace")
            raise SystemExit(f"hub {path}: HTTP {error.code} {detail}") from None
        return dict(payload.get("data") or {})


def credentials() -> tuple[str, str]:
    values = {key: os.environ.get(key, "") for key in ("INITIAL_ADMIN_USER", "INITIAL_ADMIN_PASSWORD")}
    env_file = REPO / ".env"
    if not all(values.values()) and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in values and not values[key.strip()]:
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values["INITIAL_ADMIN_USER"], values["INITIAL_ADMIN_PASSWORD"]


def login(url: str) -> str:
    token = os.environ.get("ANANTA_HUB_TOKEN", "")
    if token:
        return token
    user, password = credentials()
    if not user or not password:
        raise SystemExit("no Hub credentials (ANANTA_HUB_TOKEN or INITIAL_ADMIN_USER/PASSWORD)")
    request = urllib.request.Request(url.rstrip("/") + "/login", method="POST",
                                     data=json.dumps({"username": user, "password": password}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return str(json.loads(response.read())["data"]["access_token"])


def upload(hub: Hub, missing: list[str], sources: dict[str, tuple[str, str]]) -> int:
    uploaded, batch, size = 0, {}, 0
    wanted = {sources[digest][0]: digest for digest in missing if digest in sources}
    paths = {blob: sources[digest][1] for blob, digest in wanted.items()}
    for blob, data in read_blobs(sorted(wanted)):
        text = redacted_text(paths[blob], data)
        if text is None or sha256(text) != wanted[blob]:
            raise SystemExit(f"content changed while syncing: {paths[blob]}")
        if batch and size + len(text) > UPLOAD_BATCH_CHARS:
            uploaded += hub.post(f"{API}/content", {"texts": batch})["received"]
            batch, size = {}, 0
        batch[wanted[blob]] = text
        size += len(text)
    if batch:
        uploaded += hub.post(f"{API}/content", {"texts": batch})["received"]
    return uploaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--profile", default=os.environ.get("ANANTA_CODECOMPASS_LAYER_PROFILE", "ananta-project"))
    parser.add_argument("--hub", default=os.environ.get("ANANTA_HUB_URL", "http://localhost:5000"))
    parser.add_argument("--dry-run", action="store_true", help="build the manifest only")
    args = parser.parse_args(argv)

    commit = git("rev-parse", args.commit).decode().strip()
    cache = BlobCache(REPO / ".git" / "codecompass-layer-sync-cache.json")
    manifest, sources = build_manifest(commit, cache)
    cache.save()
    files, snapshot = len(manifest["files"]), manifest["snapshot_revision"][:12]
    print(f"commit {commit[:12]}: {files} indexed files, snapshot {snapshot}")
    if args.dry_run:
        return 0
    hub = Hub(args.hub, login(args.hub))
    accepted = hub.post(f"{API}/snapshots", {"manifest": manifest})
    uploaded = upload(hub, list(accepted.get("missing_content_sha256") or []), sources)
    result = hub.post(f"{API}/commits", {"profile_id": args.profile, "snapshot_ref": accepted["snapshot_ref"],
                                         "commit_sha": commit})
    print(f"uploaded {uploaded} contents; hub: {result.get('status')} "
          f"{result.get('decision') or ''} {result.get('file_changes', '')} {result.get('task_id') or ''}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
