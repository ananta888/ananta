"""Reusable Hub-to-production-worker harness for mail provider E2Es."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.jmap_http_transport import JmapHttpRequest, JmapHttpResponse
from agent.services.mail_account_service import MailAccountService
from agent.services.mail_contract_service import MailAccountV2, MailMessageRefV2
from agent.services.mail_migration_service import (
    MailMigrationCommand,
    MailMigrationService,
)
from agent.services.mail_runtime_policy import (
    MailCircuitBreaker,
    MailRuntimeAvailabilityPolicy,
    MailRuntimePolicy,
)
from agent.services.mail_task_service import (
    InMemoryMailAccountLeaseStore,
    MailTaskService,
    MailWorkspaceScope,
)
from worker.mail_sync_transaction_adapter import SqliteMailMetadataStore
from worker.mail_task_composition import build_production_mail_task_execution
from worker.mail_task_execution import MailWorkerTaskHandler


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get_by_id(self, task_id: str) -> dict[str, Any] | None:
        return self.rows.get(task_id)

    def get_all(self) -> list[dict[str, Any]]:
        return list(self.rows.values())


class _Queue:
    def __init__(self, repository: _Repository) -> None:
        self._repository = repository

    def ingest_task(self, **kwargs: Any) -> None:
        self._repository.rows[str(kwargs["task_id"])] = {
            "id": kwargs["task_id"],
            "status": kwargs["status"],
            **dict(kwargs["extra_fields"]),
            "verification_status": {},
        }


class InProcessIntentHub:
    """Minimal HTTP representation of the Hub-owned intent/grant boundary."""

    token = "contract-hub-token"

    def __init__(self) -> None:
        self._intents: dict[str, dict[str, Any]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("intent_hub_not_started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def register(
        self,
        *,
        intent_ref: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._intents[str(intent_ref)] = {
            "operation": str(operation),
            "payload": dict(payload),
        }

    def __enter__(self) -> "InProcessIntentHub":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.headers.get("Authorization") != f"Bearer {owner.token}":
                    self._reply(401, {"reason_code": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self._reply(400, {"reason_code": "invalid_length"})
                    return
                if length < 1 or length > 1024 * 1024:
                    self._reply(400, {"reason_code": "invalid_length"})
                    return
                try:
                    request = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._reply(400, {"reason_code": "invalid_json"})
                    return
                if self.path == "/api/mail/internal/intents/resolve":
                    self._resolve(request)
                    return
                if self.path == "/api/mail/internal/intents/authorize-content":
                    self._authorize(request)
                    return
                self._reply(404, {"reason_code": "not_found"})

            def _resolve(self, request: Mapping[str, Any]) -> None:
                intent_ref = str(request.get("intent_ref") or "")
                registered = owner._intents.get(intent_ref)
                operation = str(request.get("operation") or "")
                if registered is None or registered["operation"] != operation:
                    self._reply(404, {"reason_code": "intent_not_found"})
                    return
                workspace = dict(request.get("workspace_scope") or {})
                account_ref = str(request.get("account_ref") or "")
                account_id = account_ref.split(":", 1)[-1]
                self._reply(
                    200,
                    {
                        "ok": True,
                        "intent": {
                            "intent_ref": intent_ref,
                            "operation": operation,
                            "account_id": account_id,
                            "workspace_id": str(workspace.get("workspace_id") or ""),
                            "grant_ref": f"grant-{intent_ref}",
                            "payload": dict(registered["payload"]),
                            "expires_at": time.time() + 300.0,
                            "job_id": str(request.get("job_id") or ""),
                        }
                    },
                )

            def _authorize(self, request: Mapping[str, Any]) -> None:
                access = dict(request.get("access_request") or {})
                if not all(
                    str(access.get(field) or "")
                    for field in (
                        "account_id",
                        "workspace_id",
                        "artifact_ref",
                        "mail_ref_id",
                        "grant_ref",
                        "release_scope",
                    )
                ):
                    self._reply(400, {"reason_code": "access_context_incomplete"})
                    return
                expires = (
                    datetime.now(UTC) + timedelta(minutes=5)
                ).isoformat().replace("+00:00", "Z")
                self._reply(
                    200,
                    {
                        "ok": True,
                        "decision": {
                            "allowed": True,
                            "reason_code": "mail_content_access_allowed",
                            "policy_decision_ref": "policy-decision-contract",
                            "expires_at": expires,
                            "nonce": "nonce-contract",
                        }
                    },
                )

            def _reply(self, status: int, payload: Mapping[str, Any]) -> None:
                body = json.dumps(dict(payload), separators=(",", ":")).encode(
                    "utf-8"
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mail-intent-contract",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


class ContractJmapAdapter:
    """Deterministic in-process JMAP server behind the real JMAP transport."""

    def __init__(self) -> None:
        self.email_state = "email-state-1"
        self.query_state = "query-state-1"
        self.mailbox_state = "mailbox-state-1"
        self.messages: dict[str, dict[str, Any]] = {
            "E1": self._message(
                email_id="E1",
                subject="Project lighthouse",
                message_id="lighthouse-1@example.test",
                keywords={"$seen": True},
            ),
            "E2": self._message(
                email_id="E2",
                subject="Re: Project lighthouse",
                message_id="lighthouse-2@example.test",
                keywords={"$answered": True},
            ),
        }
        self.calls: list[str] = []

    def send(self, request: JmapHttpRequest) -> JmapHttpResponse:
        if request.method == "GET":
            return self._response(request, self._session())
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        responses: list[list[Any]] = []
        for method, arguments, call_id in payload.get("methodCalls", []):
            self.calls.append(str(method))
            responses.append(
                [
                    method,
                    self._method(str(method), dict(arguments)),
                    call_id,
                ]
            )
        return self._response(request, {"methodResponses": responses})

    def _method(self, method: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if method == "Mailbox/get":
            rows = (
                []
                if arguments.get("ids") == []
                else [
                    {
                        "id": "M-INBOX",
                        "name": "Inbox",
                        "role": "inbox",
                        "sortOrder": 10,
                        "totalEmails": len(self.messages),
                        "unreadEmails": 1,
                        "myRights": {
                            "mayReadItems": True,
                            "mayAddItems": True,
                            "maySetSeen": True,
                            "maySetKeywords": True,
                            "mayDelete": True,
                        },
                    },
                    {
                        "id": "M-PROJECTS",
                        "name": "Projects",
                        "role": None,
                        "sortOrder": 20,
                        "totalEmails": 1,
                        "unreadEmails": 0,
                        "myRights": {
                            "mayReadItems": True,
                            "mayAddItems": True,
                            "maySetSeen": True,
                            "maySetKeywords": True,
                            "mayDelete": True,
                        },
                    },
                ]
            )
            return {"accountId": "A1", "state": self.mailbox_state, "list": rows}
        if method == "Email/query":
            ids = sorted(self.messages)
            return {
                "accountId": "A1",
                "queryState": self.query_state,
                "canCalculateChanges": True,
                "position": 0,
                "ids": ids,
                "total": len(ids),
            }
        if method == "Email/get":
            ids = list(arguments.get("ids") or [])
            properties = set(arguments.get("properties") or [])
            rows = [
                self._project_message(self.messages[email_id], properties)
                for email_id in ids
                if email_id in self.messages
            ]
            return {
                "accountId": "A1",
                "state": self.email_state,
                "list": rows,
                "notFound": [],
            }
        if method == "Email/set":
            old_state = self.email_state
            updated: dict[str, Any] = {}
            for email_id, patch in dict(arguments.get("update") or {}).items():
                message = self.messages[email_id]
                for path, value in dict(patch).items():
                    if path.startswith("keywords/"):
                        keyword = path.split("/", 1)[1]
                        if value is None:
                            message["keywords"].pop(keyword, None)
                        else:
                            message["keywords"][keyword] = bool(value)
                updated[email_id] = None
            self.email_state = "email-state-2"
            return {
                "accountId": "A1",
                "oldState": old_state,
                "newState": self.email_state,
                "updated": updated,
                "notUpdated": {},
            }
        if method == "Mailbox/changes":
            return {
                "accountId": "A1",
                "oldState": arguments.get("sinceState"),
                "newState": self.mailbox_state,
                "hasMoreChanges": False,
                "created": [],
                "updated": [],
                "destroyed": [],
            }
        return {
            "type": "unknownMethod",
            "description": "unsupported contract method",
        }

    @staticmethod
    def _message(
        *,
        email_id: str,
        subject: str,
        message_id: str,
        keywords: Mapping[str, bool],
    ) -> dict[str, Any]:
        return {
            "id": email_id,
            "blobId": f"B-{email_id}",
            "threadId": "T-LIGHTHOUSE",
            "mailboxIds": {"M-INBOX": True},
            "keywords": dict(keywords),
            "size": 128,
            "receivedAt": "2025-01-14T09:00:00Z",
            "messageId": [message_id],
            "from": [{"email": "alice@example.test"}],
            "to": [{"email": "bob@example.test"}],
            "subject": subject,
            "bodyStructure": {
                "partId": "1",
                "blobId": f"B-{email_id}",
                "type": "text/plain",
                "size": 32,
            },
            "textBody": [{"partId": "1", "type": "text/plain"}],
            "htmlBody": [],
            "bodyValues": {
                "1": {
                    "value": "deterministic contract body",
                    "isTruncated": False,
                }
            },
            "attachments": [],
        }

    @staticmethod
    def _project_message(
        message: Mapping[str, Any],
        properties: set[str],
    ) -> dict[str, Any]:
        if not properties:
            return dict(message)
        return {
            key: value
            for key, value in message.items()
            if key in properties or key == "id"
        }

    @staticmethod
    def _session() -> dict[str, Any]:
        core = {
            "maxSizeRequest": 100000,
            "maxConcurrentRequests": 4,
            "maxCallsInRequest": 16,
            "maxObjectsInGet": 50,
            "maxObjectsInSet": 20,
        }
        return {
            "capabilities": {
                "urn:ietf:params:jmap:core": core,
                "urn:ietf:params:jmap:mail": {},
            },
            "accounts": {
                "A1": {
                    "name": "alice@example.test",
                    "isPersonal": True,
                    "accountCapabilities": {
                        "urn:ietf:params:jmap:mail": {}
                    },
                }
            },
            "primaryAccounts": {"urn:ietf:params:jmap:mail": "A1"},
            "apiUrl": "http://127.0.0.1:18080/jmap",
            "downloadUrl": "http://127.0.0.1:18080/download/{accountId}/{blobId}/{name}?accept={type}",
            "uploadUrl": "http://127.0.0.1:18080/upload/{accountId}",
            "eventSourceUrl": (
                "http://127.0.0.1:18080/events"
                "?types={types}&closeafter={closeafter}&ping={ping}"
            ),
            "state": "session-state-1",
        }

    @staticmethod
    def _response(
        request: JmapHttpRequest,
        payload: Mapping[str, Any],
    ) -> JmapHttpResponse:
        return JmapHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
            final_url=request.url,
        )


class AnantaMailCompositionHarness:
    def __init__(
        self,
        *,
        data_root: Path,
        secret_root: Path,
        password_file: Path | None,
        intent_hub: InProcessIntentHub,
        session_url: str = "http://127.0.0.1:18080/.well-known/jmap",
        account_id: str = "jmap-fixture-alice",
        display_name: str = "JMAP Fixture Alice",
        username_ref: str = "env://ANANTA_JMAP_FIXTURE_ALICE_USER",
        credential_ref: str | None = None,
        auth_mode: str = "basic",
        provider_account_id: str | None = None,
        runtime_environment: Mapping[str, str] | None = None,
        external_network_enabled: bool = False,
        local_endpoints_enabled: bool = True,
        workspace_id: str = "jmap-contract-e2e",
        actor: str = "jmap-contract-e2e",
        sync_fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.data_root = data_root.resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        resolved_credential_ref = credential_ref
        if resolved_credential_ref is None:
            if password_file is None:
                raise ValueError("mail_harness_credential_ref_required")
            resolved_credential_ref = password_file.resolve().as_uri()
        provider_config: dict[str, Any] = {
            "session_url": session_url,
            "auth_mode": auth_mode,
        }
        if provider_account_id:
            provider_config["provider_account_id"] = provider_account_id
        self.account_id = str(account_id)
        self._username_ref = str(username_ref)
        self._workspace_id = str(workspace_id)
        self._actor = str(actor)
        self._counter = 0
        self._repository = _Repository()
        queue = _Queue(self._repository)

        def update(task_id: str, status: str, **kwargs: Any) -> dict[str, Any]:
            self._repository.rows[task_id].update({"status": status, **kwargs})
            return self._repository.rows[task_id]

        self.hub = MailTaskService(
            task_queue=queue,
            task_repository=self._repository,
            lease_store=InMemoryMailAccountLeaseStore(),
            status_updater=update,
            audit=lambda _event, _payload: None,
            clock=time.time,
            role="hub",
        )
        self.accounts = MailAccountService(
            store_path=self.data_root / "accounts-v2.json"
        )
        self.accounts.upsert_account(
            MailAccountV2(
                account_id=self.account_id,
                display_name=display_name,
                requested_protocol="jmap",
                resolved_protocol="jmap",
                username_ref=self._username_ref,
                credential_ref=resolved_credential_ref,
                sync_policy="headers_only",
                enabled=True,
                provider_config=provider_config,
            )
        )
        self.intent_hub = intent_hub
        execution_environment = {
            str(key): str(value)
            for key, value in dict(runtime_environment or {}).items()
        }
        execution_environment.update(
            {
                "ANANTA_REPO_ROOT": str(self.data_root.parent),
                "ANANTA_MAIL_DATA_ROOT": str(self.data_root),
                "ANANTA_MAIL_ENABLED": "1",
                "ANANTA_MAIL_JMAP_ENABLED": "1",
                "ANANTA_MAIL_IMAP_FALLBACK_ENABLED": "1",
                "ANANTA_MAIL_EXTERNAL_NETWORK_ENABLED": (
                    "1" if external_network_enabled else "0"
                ),
                "ANANTA_MAIL_LOCAL_ENDPOINTS_ENABLED": (
                    "1" if local_endpoints_enabled else "0"
                ),
                "ANANTA_MAIL_ALLOWED_LOCAL_HOSTS": (
                    "127.0.0.1,localhost" if local_endpoints_enabled else ""
                ),
                "ANANTA_MAIL_ALLOWED_LOCAL_CIDRS": (
                    "127.0.0.0/8" if local_endpoints_enabled else ""
                ),
                "ANANTA_MAIL_SECRET_ROOTS": str(secret_root.resolve()),
                "ANANTA_JMAP_FIXTURE_ALICE_USER": "alice@example.test",
                "ANANTA_MAIL_HUB_URL": intent_hub.url,
                "ANANTA_MAIL_HUB_TOKEN": intent_hub.token,
            }
        )
        self.execution = build_production_mail_task_execution(
            environ=execution_environment,
            runtime_availability=MailRuntimeAvailabilityPolicy(
                runtime_policy=MailRuntimePolicy.from_environment(
                    {"ANANTA_MAIL_ROLLOUT_PHASE": "operator_opt_in"}
                ),
                circuit_breaker=MailCircuitBreaker(
                    state_path=self.data_root / "circuit-breakers.json"
                ),
            ),
            sync_fault_injector=sync_fault_injector,
        )
        self.worker = MailWorkerTaskHandler(self.execution, clock=time.time)
        self.metadata = SqliteMailMetadataStore(
            database_path=self.data_root / "runtime-state-v1.sqlite3"
        )

    def add_auto_account(self, *, resolved_protocol: str | None) -> None:
        self.accounts.create_account(
            MailAccountV2(
                account_id=(
                    "auto-unresolved"
                    if resolved_protocol is None
                    else f"auto-{resolved_protocol}"
                ),
                display_name="Auto protocol",
                requested_protocol="auto",
                resolved_protocol=resolved_protocol,
                username_ref=self._username_ref,
                credential_ref=self._username_ref,
                sync_policy="manual",
                enabled=True,
                provider_config={
                    "imap": {
                        "host": "127.0.0.1",
                        "port": 9,
                        "tls_mode": "require_tls",
                    }
                },
            )
        )

    def run(
        self,
        operation: str,
        *,
        account_id: str | None = None,
        intent_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        selected_account_id = str(account_id or self.account_id)
        operation_refs: dict[str, str] = {}
        if intent_payload is not None:
            intent_ref = f"intent-{operation}-{self._counter}"
            self.intent_hub.register(
                intent_ref=intent_ref,
                operation=operation,
                payload=intent_payload,
            )
            operation_refs["intent_ref"] = intent_ref
        queued = self.hub.submit(
            operation=operation,
            account_ref=f"mail-account:{selected_account_id}",
            workspace_scope=MailWorkspaceScope(self._workspace_id),
            idempotency_key=(
                f"mail-{operation}-{selected_account_id}-{self._counter}"
            ),
            policy_refs={
                f"{operation}_policy_ref": f"policy:mail:{operation}:v1"
            },
            operation_refs=operation_refs,
            actor=self._actor,
        )
        job_id = str(queued["job_id"])
        owner = f"hub-worker:{self._actor}"
        lease = self.hub.claim_for_delegation(
            job_id=job_id,
            owner_ref=owner,
        )
        if lease is None:
            raise AssertionError("mail lease was not acquired")
        raw = self._repository.rows[job_id]
        raw["worker_execution_context"]["mail_task_control"]["lease"] = lease
        result = self.worker.execute(tid=job_id, task=raw)
        accepted = self.hub.validate_worker_result(job_id=job_id, result=result)
        if not self.hub.release_lease(
            job_id=job_id,
            fencing_token=int(accepted["lease_fencing_token"]),
            owner_ref=owner,
        ):
            raise AssertionError("mail lease was not released")
        return accepted

    def message_ref(self, *, subject: str) -> MailMessageRefV2:
        matches = [
            row
            for row in self.metadata.list_messages(account_id=self.account_id)
            if str(dict(row.get("metadata") or {}).get("subject") or "") == subject
        ]
        if len(matches) != 1:
            raise AssertionError("expected exactly one fixture message")
        return MailMessageRefV2.from_mapping(matches[0]["message_ref"])

    def first_message(
        self,
    ) -> tuple[MailMessageRefV2, Mapping[str, Any]]:
        rows = sorted(
            self.metadata.list_messages(account_id=self.account_id),
            key=lambda row: json.dumps(row.get("message_ref") or {}, sort_keys=True),
        )
        if not rows:
            raise AssertionError("expected at least one synced message")
        return (
            MailMessageRefV2.from_mapping(rows[0]["message_ref"]),
            dict(rows[0].get("metadata") or {}),
        )

    def email_state(self) -> str:
        cursor = self.metadata.get_sync_cursor(
            account_id=self.account_id,
            protocol="jmap",
            scope="default",
        )
        if cursor is None or not cursor.email_state:
            raise AssertionError("sync cursor missing")
        return cursor.email_state


def run_fixture_migration_and_restore(root: Path) -> dict[str, str]:
    legacy_root = root / "legacy"
    target_root = root / "target"
    legacy_root.mkdir(parents=True, exist_ok=True)
    accounts = legacy_root / "accounts.json"
    metadata = legacy_root / "metadata.json"
    accounts.write_text(
        json.dumps(
            {
                "schema": "imap_accounts.v1",
                "accounts": [
                    {
                        "account_id": "legacy-jmap-fixture",
                        "display_name": "Legacy fixture",
                        "host": "imap.example.test",
                        "port": 993,
                        "username_ref": "env://ANANTA_JMAP_FIXTURE_ALICE_USER",
                        "credential_ref": "secret://fixture/jmap",
                        "auth_mode": "password_app_token",
                        "tls_mode": "require_tls",
                        "sync_policy": "headers_only",
                        "enabled": True,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    metadata.write_text(
        json.dumps(
            {
                "schema": "imap_metadata_store.v1",
                "messages": [
                    {
                        "message_ref": {
                            "account_id": "legacy-jmap-fixture",
                            "mailbox": "INBOX",
                            "uid": 1,
                            "message_id": "<lighthouse-1@example.test>",
                            "date": "2025-01-14T09:00:00Z",
                            "from": "alice@example.test",
                            "to": "bob@example.test",
                            "subject_hash": "fixture-hash",
                        },
                        "header_meta": {"subject": "Project lighthouse"},
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    command = MailMigrationCommand(
        command_id="jmap-fixture-migration",
        legacy_accounts_path=accounts,
        legacy_metadata_path=metadata,
        target_accounts_path=target_root / "accounts.json",
        target_metadata_path=target_root / "metadata.json",
        journal_path=target_root / "journal.json",
        dry_run=False,
    )
    service = MailMigrationService()
    applied = service.execute(command)
    restored = service.restore(
        command,
        migration_id=applied.migration_id,
        approval_ref="approval-jmap-fixture-restore",
    )
    if applied.status != "complete" or restored.status != "restored":
        raise AssertionError("fixture migration or restore failed")
    if command.target_accounts_path.exists() or command.target_metadata_path.exists():
        raise AssertionError("fixture restore left target state")
    return {
        "migration_status": applied.status,
        "migration_reason_code": applied.reason_code,
        "restore_status": restored.status,
        "restore_reason_code": restored.reason_code,
    }


def assert_reference_only_results(
    results: Sequence[Mapping[str, Any]],
    *,
    forbidden_values: Sequence[str],
) -> None:
    serialized = json.dumps([dict(item) for item in results], sort_keys=True)
    for forbidden in forbidden_values:
        if forbidden and forbidden in serialized:
            raise AssertionError("mail result leaked content or credential")


__all__ = [
    "AnantaMailCompositionHarness",
    "ContractJmapAdapter",
    "InProcessIntentHub",
    "assert_reference_only_results",
    "run_fixture_migration_and_restore",
]
