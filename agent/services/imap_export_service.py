from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_component(text: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(text or "").strip())
    return value.strip("._") or "mail"


def export_mail_payload(
    *,
    message_ref: dict[str, Any],
    header_meta: dict[str, Any],
    body_text: str,
    format_name: str,
    include_body: bool,
    export_dir: str | Path,
) -> dict[str, Any]:
    fmt = str(format_name or "").strip().lower()
    if fmt not in {"json", "text", "eml"}:
        raise ValueError("mail_export_format_invalid")
    ref = dict(message_ref or {})
    account_id = _safe_component(str(ref.get("account_id") or "acc"))
    mailbox = _safe_component(str(ref.get("mailbox") or "mailbox"))
    uid = int(ref.get("uid") or 0)
    stamp = _now_iso().replace(":", "").replace("-", "")
    extension = {"json": "json", "text": "txt", "eml": "eml"}[fmt]
    filename = f"mail-{account_id}-{mailbox}-{uid}-{stamp}.{extension}"
    root = Path(export_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = (root / filename).resolve()
    if root not in destination.parents and destination != root:
        raise ValueError("mail_export_path_invalid")
    safe_body = str(body_text or "") if include_body else ""
    if fmt == "json":
        payload = {
            "schema": "mail_export.v1",
            "message_ref": {
                "account_id": str(ref.get("account_id") or ""),
                "mailbox": str(ref.get("mailbox") or ""),
                "uid": int(ref.get("uid") or 0),
                "message_id": str(ref.get("message_id") or ""),
                "date": str(ref.get("date") or ""),
                "from": str(ref.get("from") or ""),
                "to": str(ref.get("to") or ""),
            },
            "header_meta": dict(header_meta or {}),
            "body": safe_body,
            "body_included": bool(include_body),
            "exported_at": _now_iso(),
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    elif fmt == "text":
        rendered = (
            f"From: {ref.get('from') or ''}\n"
            f"To: {ref.get('to') or ''}\n"
            f"Date: {ref.get('date') or ''}\n"
            f"Subject: {header_meta.get('subject') or ''}\n"
            "\n"
            f"{safe_body}\n"
        )
    else:
        rendered = (
            f"From: {ref.get('from') or ''}\n"
            f"To: {ref.get('to') or ''}\n"
            f"Date: {ref.get('date') or ''}\n"
            f"Message-ID: {ref.get('message_id') or ''}\n"
            f"Subject: {header_meta.get('subject') or ''}\n"
            "MIME-Version: 1.0\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            f"{safe_body}\n"
        )
    destination.write_text(rendered, encoding="utf-8")
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return {
        "export_ref": str(destination),
        "format": fmt,
        "sha256": digest,
        "body_included": bool(include_body),
    }
