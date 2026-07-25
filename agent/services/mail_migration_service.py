from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent.services.mail_account_mapper import MailAccountMapper
from agent.services.mail_contract_service import MailMessageMetadata, MailMessageRefV2
from agent.services.mail_legacy_mapper import LegacyMailRecord, MailLegacyMapper
from agent.services.mail_metadata_store_service import locator_alias_for_ref
from agent.services.mail_migration_journal import MailFileLock, MailMigrationJournal, MailMultiFileTransaction


@dataclass(frozen=True, slots=True)
class MailMigrationCommand:
    command_id: str
    legacy_accounts_path: Path
    legacy_metadata_path: Path
    target_accounts_path: Path
    target_metadata_path: Path
    journal_path: Path
    dry_run: bool = True
    legacy_artifacts_path: Path | None = None
    target_artifacts_path: Path | None = None
    target_grant_aliases_path: Path | None = None
    global_lock_path: Path | None = None


@dataclass(frozen=True, slots=True)
class MailMigrationReport:
    migration_id: str
    status: str
    dry_run: bool
    migrated: int
    skipped: int
    conflicted: int
    failed: int
    backup_dir: str = ""
    reason_code: str = "ok"
    matched: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    alias_count: int = 0
    source_hashes: Mapping[str, str] = field(default_factory=dict)
    backup_hashes: Mapping[str, str] = field(default_factory=dict)
    target_hashes: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "migrated": self.migrated,
            "skipped": self.skipped,
            "conflicted": self.conflicted,
            "failed": self.failed,
            "backup_dir": self.backup_dir,
            "reason_code": self.reason_code,
            "matched": self.matched,
            "ambiguous": self.ambiguous,
            "unmatched": self.unmatched,
            "alias_count": self.alias_count,
            "source_hashes": dict(self.source_hashes),
            "backup_hashes": dict(self.backup_hashes),
            "target_hashes": dict(self.target_hashes),
        }


class MailMigrationService:
    def _migration_id(self, command: MailMigrationCommand) -> str:
        material = "|".join(
            (
                command.command_id,
                str(command.legacy_accounts_path.resolve()),
                str(command.legacy_metadata_path.resolve()),
                str(command.target_accounts_path.resolve()),
                str(command.target_metadata_path.resolve()),
            )
        )
        return f"mailmig-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        content = (json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _resolved_paths(command: MailMigrationCommand) -> dict[str, Path]:
        root = command.target_accounts_path.parent
        return {
            "accounts": command.target_accounts_path,
            "metadata": command.target_metadata_path,
            "artifacts": command.target_artifacts_path or root / "mail-artifacts.json",
            "grant_aliases": command.target_grant_aliases_path or root / "grant-aliases.json",
            "journal": command.journal_path,
        }

    @staticmethod
    def _legacy_artifacts(command: MailMigrationCommand) -> Path:
        return command.legacy_artifacts_path or command.legacy_accounts_path.parent / "mail-artifacts.json"

    @staticmethod
    def _read_rows(path: Path, key: str) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("mail_legacy_store_invalid")
        return [dict(item) for item in list(payload.get(key) or []) if isinstance(item, dict)]

    @staticmethod
    def _backup(
        command: MailMigrationCommand,
        migration_id: str,
        source_paths: Mapping[str, Path],
        target_paths: Mapping[str, Path],
    ) -> tuple[Path, dict[str, str], dict[str, str]]:
        root = command.target_accounts_path.parent / ".migration-backup" / migration_id
        root.mkdir(parents=True, exist_ok=True)
        source_hashes: dict[str, str] = {}
        backup_hashes: dict[str, str] = {}
        manifest: dict[str, Any] = {"schema": "mail_migration_backup.v1", "sources": {}, "targets": {}}
        for name, source in source_paths.items():
            if source.exists():
                destination = root / f"source-{name}.bin"
                shutil.copy2(source, destination)
                source_hashes[name] = MailMigrationService._hash(source)
                backup_hashes[name] = MailMigrationService._hash(destination)
                if source_hashes[name] != backup_hashes[name]:
                    raise ValueError("mail_migration_backup_hash_mismatch")
                manifest["sources"][name] = {"path": str(destination), "sha256": backup_hashes[name]}
        for name, target in target_paths.items():
            destination = root / f"preimage-{name}.bin"
            existed = target.exists()
            if existed:
                shutil.copy2(target, destination)
            manifest["targets"][name] = {
                "path": str(target.resolve()),
                "preimage": str(destination),
                "existed": existed,
                "sha256": MailMigrationService._hash(destination) if existed else "",
            }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return root, source_hashes, backup_hashes

    @staticmethod
    def _load_target(path: Path, *, schema: str, defaults: Mapping[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return {"schema": schema, **dict(defaults)}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != schema:
            raise ValueError("mail_migration_target_schema_unsupported")
        return payload

    @staticmethod
    def _artifact_aliases(records: list[LegacyMailRecord]) -> list[dict[str, str]]:
        scopes = {
            "metadata_only": "metadata_only",
            "excerpt": "body_excerpt",
            "body_excerpt": "body_excerpt",
            "full_body": "full_body",
            "attachment_ref": "attachment_ref",
        }
        aliases: dict[str, dict[str, str]] = {}
        for record in records:
            locator = dict(record.message_ref.protocol_locator)
            legacy_base = f"mail://{record.message_ref.account_id}/{locator.get('mailbox')}/{locator.get('uid')}"
            current_base = f"mail://{record.message_ref.mail_ref_id}"
            aliases[legacy_base] = {
                "legacy_artifact_ref": legacy_base,
                "artifact_ref": current_base,
                "mail_ref_id": record.message_ref.mail_ref_id,
            }
            for old_scope, new_scope in scopes.items():
                old_ref = f"{legacy_base}?scope={old_scope}"
                aliases[old_ref] = {
                    "legacy_artifact_ref": old_ref,
                    "artifact_ref": f"{current_base}?scope={new_scope}",
                    "mail_ref_id": record.message_ref.mail_ref_id,
                }
        return [aliases[key] for key in sorted(aliases)]

    def execute(self, command: MailMigrationCommand) -> MailMigrationReport:
        migration_id = self._migration_id(command)
        target_paths = self._resolved_paths(command)
        source_paths = {
            "accounts": command.legacy_accounts_path,
            "metadata": command.legacy_metadata_path,
            "artifacts": self._legacy_artifacts(command),
        }
        lock_path = command.global_lock_path or command.target_accounts_path.parent / ".mail-migration.lock"
        with MailFileLock(path=lock_path, timeout_seconds=0.25):
            transaction = MailMultiFileTransaction(
                transaction_root=command.target_accounts_path.parent / ".mail-transactions",
                transaction_id=migration_id,
            )
            resumed = transaction.recover_if_needed()
            previous = MailMigrationJournal(path=command.journal_path).get(migration_id)
            if previous is not None and previous.status == "complete":
                return MailMigrationReport(
                    migration_id=migration_id,
                    status="complete",
                    dry_run=command.dry_run,
                    migrated=previous.migrated,
                    skipped=previous.skipped,
                    conflicted=previous.conflicted,
                    failed=previous.failed,
                    backup_dir=str(command.target_accounts_path.parent / ".migration-backup" / migration_id),
                    reason_code="already_complete",
                    matched=previous.matched,
                    ambiguous=previous.ambiguous,
                    unmatched=previous.unmatched,
                    alias_count=previous.alias_count,
                    source_hashes=dict(previous.source_hashes or {}),
                    backup_hashes=dict(previous.backup_hashes or {}),
                    target_hashes=dict(previous.target_hashes or {}),
                )
            raw_accounts = self._read_rows(command.legacy_accounts_path, "accounts")
            raw_messages = self._read_rows(command.legacy_metadata_path, "messages")
            try:
                mapped_accounts = [MailAccountMapper.from_legacy_imap(item) for item in raw_accounts]
                mapped_messages = [MailLegacyMapper.row_from_v1(item) for item in raw_messages]
                failed = 0
            except (TypeError, ValueError):
                mapped_accounts = []
                mapped_messages = []
                failed = 1
            source_hashes = {name: self._hash(path) for name, path in source_paths.items() if path.exists()}
            if command.dry_run:
                return MailMigrationReport(
                    migration_id=migration_id,
                    status="dry_run",
                    dry_run=True,
                    migrated=len(mapped_accounts) + len(mapped_messages),
                    skipped=0,
                    conflicted=0,
                    failed=failed,
                    reason_code="dry_run_complete" if not failed else "dry_run_has_failures",
                    unmatched=len(mapped_messages),
                    source_hashes=source_hashes,
                )
            backup, source_hashes, backup_hashes = self._backup(
                command, migration_id, source_paths, target_paths
            )
            accounts_payload = self._load_target(
                target_paths["accounts"], schema="mail_accounts.v2", defaults={"accounts": []}
            )
            metadata_payload = self._load_target(
                target_paths["metadata"],
                schema="mail_metadata_store.v2",
                defaults={"messages": [], "sync_cursors": [], "locator_aliases": []},
            )
            artifacts_payload = self._load_target(
                target_paths["artifacts"],
                schema="ananta.mail-artifacts.v2",
                defaults={"artifacts": [], "artifact_aliases": []},
            )
            grant_payload = self._load_target(
                target_paths["grant_aliases"],
                schema="mail_grant_aliases.v1",
                defaults={"aliases": []},
            )
            migrated = skipped = conflicted = matched = ambiguous = unmatched = 0
            existing_accounts = {
                str(item.get("account_id")): dict(item)
                for item in accounts_payload["accounts"]
                if isinstance(item, dict)
            }
            for account in mapped_accounts:
                prior = existing_accounts.get(account.account_id)
                if prior is None:
                    existing_accounts[account.account_id] = account.to_dict()
                    migrated += 1
                elif prior == account.to_dict():
                    skipped += 1
                else:
                    conflicted += 1
            accounts_payload["accounts"] = [existing_accounts[key] for key in sorted(existing_accounts)]
            existing_records = [
                LegacyMailRecord(
                    MailMessageRefV2.from_mapping(dict(item.get("message_ref") or {})),
                    MailMessageMetadata.from_mapping(dict(item.get("metadata") or {})),
                )
                for item in metadata_payload["messages"]
                if isinstance(item, dict)
            ]
            strong_counts: dict[str, int] = {}
            for record in mapped_messages:
                fingerprint = MailLegacyMapper.conservative_fingerprint(record)
                if fingerprint:
                    strong_counts[fingerprint] = strong_counts.get(fingerprint, 0) + 1
            message_rows = [dict(item) for item in metadata_payload["messages"] if isinstance(item, dict)]
            aliases = [dict(item) for item in metadata_payload["locator_aliases"] if isinstance(item, dict)]
            accepted_records: list[LegacyMailRecord] = []
            for record in mapped_messages:
                fingerprint = MailLegacyMapper.conservative_fingerprint(record)
                if fingerprint and strong_counts.get(fingerprint, 0) > 1:
                    ambiguous += 1
                    conflicted += 1
                    continue
                decision = MailLegacyMapper.classify_match(record, existing_records)
                target_ref_id = decision.mail_ref_id or record.message_ref.mail_ref_id
                if decision.outcome == "ambiguous":
                    ambiguous += 1
                    conflicted += 1
                    continue
                if decision.outcome == "matched":
                    matched += 1
                    skipped += 1
                else:
                    unmatched += 1
                    migrated += 1
                    message_rows.append(
                        {
                            "message_ref": record.message_ref.to_dict(),
                            "metadata": record.metadata.to_dict(),
                            "stale": False,
                            "body": {},
                            "body_scope": "metadata_only",
                            "attachments": [],
                        }
                    )
                    existing_records.append(record)
                alias_ref = (
                    record.message_ref
                    if target_ref_id == record.message_ref.mail_ref_id
                    else MailMessageRefV2(
                        target_ref_id,
                        record.message_ref.account_id,
                        record.message_ref.protocol,
                        record.message_ref.protocol_locator,
                        record.message_ref.locator_version,
                        record.message_ref.thread_ref_id,
                    )
                )
                next_version = max([int(item.get("alias_version") or 0) for item in aliases] or [0]) + 1
                alias = locator_alias_for_ref(alias_ref, alias_version=next_version).to_dict()
                if not any(
                    str(item.get("mail_ref_id")) == alias["mail_ref_id"]
                    and dict(item.get("protocol_locator") or {}) == alias["protocol_locator"]
                    for item in aliases
                ):
                    aliases.append(alias)
                accepted_records.append(LegacyMailRecord(alias_ref, record.metadata))
            metadata_payload["messages"] = message_rows
            metadata_payload["locator_aliases"] = aliases
            artifact_aliases = self._artifact_aliases(accepted_records)
            artifacts_payload["artifact_aliases"] = artifact_aliases
            grant_payload["aliases"] = [{**item, "alias_kind": "source_artifact_grant"} for item in artifact_aliases]
            alias_count = len(aliases) + len(artifact_aliases) + len(grant_payload["aliases"])
            predicted_target_hashes = {
                str(target_paths["accounts"].resolve()): self._payload_hash(accounts_payload),
                str(target_paths["metadata"].resolve()): self._payload_hash(metadata_payload),
                str(target_paths["artifacts"].resolve()): self._payload_hash(artifacts_payload),
                str(target_paths["grant_aliases"].resolve()): self._payload_hash(grant_payload),
            }
            journal_payload = (
                json.loads(command.journal_path.read_text(encoding="utf-8"))
                if command.journal_path.exists()
                else {"schema": "mail_migration_journal.v1", "entries": {}}
            )
            journal_payload.setdefault("entries", {})[migration_id] = {
                "status": "complete",
                "account_cursor": len(mapped_accounts),
                "message_cursor": len(mapped_messages),
                "migrated": migrated,
                "skipped": skipped,
                "conflicted": conflicted,
                "failed": failed,
                "matched": matched,
                "ambiguous": ambiguous,
                "unmatched": unmatched,
                "alias_count": alias_count,
                "source_hashes": source_hashes,
                "backup_hashes": backup_hashes,
                "target_hashes": predicted_target_hashes,
            }
            files: dict[Path, Mapping[str, Any] | None] = {
                target_paths["accounts"]: accounts_payload,
                target_paths["metadata"]: metadata_payload,
                target_paths["artifacts"]: artifacts_payload,
                target_paths["grant_aliases"]: grant_payload,
                target_paths["journal"]: journal_payload,
            }
            target_hashes = dict(transaction.commit(files))
            return MailMigrationReport(
                migration_id=migration_id,
                status="complete",
                dry_run=False,
                migrated=migrated,
                skipped=skipped,
                conflicted=conflicted,
                failed=failed,
                backup_dir=str(backup),
                reason_code="resumed_after_recovery" if resumed else "ok",
                matched=matched,
                ambiguous=ambiguous,
                unmatched=unmatched,
                alias_count=alias_count,
                source_hashes=source_hashes,
                backup_hashes=backup_hashes,
                target_hashes=target_hashes,
            )

    def restore(
        self,
        command: MailMigrationCommand,
        *,
        migration_id: str,
        approval_ref: str,
    ) -> MailMigrationReport:
        if not str(approval_ref).strip():
            return MailMigrationReport(migration_id, "restore_denied", False, 0, 0, 0, 1, reason_code="restore_approval_required")
        root = command.target_accounts_path.parent / ".migration-backup" / str(migration_id)
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            return MailMigrationReport(migration_id, "restore_failed", False, 0, 0, 0, 1, reason_code="backup_not_found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        lock_path = command.global_lock_path or command.target_accounts_path.parent / ".mail-migration.lock"
        with MailFileLock(path=lock_path, timeout_seconds=0.25):
            restore_files: dict[Path, Mapping[str, Any] | None] = {}
            for item in dict(manifest.get("targets") or {}).values():
                target = Path(str(item["path"]))
                if not bool(item.get("existed")):
                    restore_files[target] = None
                    continue
                preimage = Path(str(item["preimage"]))
                if self._hash(preimage) != str(item.get("sha256") or ""):
                    return MailMigrationReport(
                        migration_id, "restore_failed", False, 0, 0, 0, 1,
                        reason_code="backup_content_hash_mismatch",
                    )
                restore_files[target] = json.loads(preimage.read_text(encoding="utf-8"))
            transaction = MailMultiFileTransaction(
                transaction_root=command.target_accounts_path.parent / ".mail-transactions",
                transaction_id=f"restore-{migration_id}",
            )
            target_hashes = transaction.commit(restore_files)
        return MailMigrationReport(
            migration_id=migration_id,
            status="restored",
            dry_run=False,
            migrated=0,
            skipped=0,
            conflicted=0,
            failed=0,
            backup_dir=str(root),
            reason_code="restore_complete",
            target_hashes=target_hashes,
        )
