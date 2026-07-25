from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.services.mail_contract_service import MailMessageMetadata, MailMessageRefV2
from agent.services.mail_provider_ports import VerifiedMailContentAccess


class MailExportService:
    def export(
        self,
        *,
        message_ref: MailMessageRefV2,
        metadata: MailMessageMetadata,
        body_text: str,
        format_name: str,
        include_body: bool,
        export_dir: str | Path,
        access: VerifiedMailContentAccess | None = None,
    ) -> dict[str, Any]:
        fmt = str(format_name).lower()
        if fmt not in {"json", "text", "eml"}:
            raise ValueError("mail_export_format_invalid")
        if include_body:
            if not isinstance(access, VerifiedMailContentAccess):
                raise PermissionError("verified_mail_content_access_required")
            if (
                access.account_id != message_ref.account_id
                or access.mail_ref_id != message_ref.mail_ref_id
                or message_ref.mail_ref_id not in access.artifact_ref
            ):
                raise PermissionError("mail_content_artifact_mismatch")
            if access.release_scope not in {"body_excerpt", "full_body"}:
                raise PermissionError("mail_content_scope_denied")
        root = Path(export_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"mail-{message_ref.mail_ref_id}.{ {'json': 'json', 'text': 'txt', 'eml': 'eml'}[fmt] }"
        safe_body = str(body_text) if include_body else ""
        meta = metadata.to_dict()
        if fmt == "json":
            rendered = json.dumps(
                {
                    "schema": "mail_export.v2",
                    "mail_ref_id": message_ref.mail_ref_id,
                    "account_id": message_ref.account_id,
                    "thread_ref_id": message_ref.thread_ref_id,
                    "metadata": meta,
                    "body": safe_body,
                    "body_included": include_body,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        elif fmt == "text":
            rendered = (
                f"From: {meta['from']}\nTo: {', '.join(meta['to'])}\nDate: {meta['date']}\n"
                f"Subject: {meta['subject']}\n\n{safe_body}\n"
            )
        else:
            rendered = (
                f"From: {meta['from']}\nTo: {', '.join(meta['to'])}\nDate: {meta['date']}\n"
                f"Message-ID: {meta['message_id_header']}\nSubject: {meta['subject']}\n"
                "MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n\n"
                f"{safe_body}\n"
            )
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return {
            "export_ref": str(destination),
            "format": fmt,
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "body_included": include_body,
            "mail_ref_id": message_ref.mail_ref_id,
        }
