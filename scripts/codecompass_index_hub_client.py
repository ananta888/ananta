"""Hub HTTP client used by ``scripts/setup_codecompass_index.py``.

Authenticates against the Hub, submits index records to the Hub task queue
and polls the delegated index job. The script only talks to the Hub; the
Hub remains the owner of the index job.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

SOURCE_SCOPE = "repo_path"


def login(hub: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    token = str((data.get("data") or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("Login failed — no access_token in response")
    return token


def post_index(
    hub: str,
    token: str,
    records: list[dict],
    source_id: str,
    *,
    source_metadata: dict | None = None,
    urlopen: Callable[..., Any] | None = None,
) -> dict:
    payload = json.dumps({
        "source_scope": SOURCE_SCOPE,
        "source_id": source_id,
        "records": records,
        # Index builds are delegated to the persistent Hub task queue.  The
        # field remains explicit for compatibility with older Hub versions.
        "async": True,
        "profile_name": "deep_code",
        "source_metadata": {
            "project": "ananta",
            "indexed_by": "setup_codecompass_index.py",
            **dict(source_metadata or {}),
        },
    }).encode()
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/knowledge/sources/index-records",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with (urlopen or urllib.request.urlopen)(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc


def get_index_job(hub: str, token: str, job_id: str) -> dict:
    encoded_job_id = urllib.parse.quote(str(job_id), safe="")
    req = urllib.request.Request(
        f"{hub.rstrip('/')}/knowledge/index-jobs/{encoded_job_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:400]}") from exc


def wait_for_index_job(
    hub: str,
    token: str,
    job_id: str,
    *,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        response = get_index_job(hub, token, job_id)
        job = dict((response.get("data") or {}).get("job") or {})
        if str(job.get("status") or "").strip().lower() in {
            "completed",
            "failed",
            "cancelled",
        }:
            return job
        if time.monotonic() >= deadline:
            return {
                **job,
                "job_id": str(job.get("job_id") or job_id),
                "status": "wait_timeout",
            }
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
