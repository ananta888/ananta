#!/usr/bin/env python3
"""Reproducible, seedable Stalwart JMAP integration-test stack.

The live image is deliberately guarded by an explicit organizational license
acknowledgement. The fixture contract can always be checked without Docker or
network access.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "tests/fixtures/jmap/stalwart-seed.json"
CORE_CAPABILITY = "urn:ietf:params:jmap:core"
MAIL_CAPABILITY = "urn:ietf:params:jmap:mail"
MANAGEMENT_CAPABILITY = "urn:stalwart:jmap"
LIVE_ACK_ENV = "ANANTA_STALWART_TEST_LICENSE_ACK"
LIVE_RUN_ENV = "RUN_STALWART_LIVE_E2E"
MAX_HTTP_RESPONSE_BYTES = 2 * 1024 * 1024


class StackFailure(RuntimeError):
    """Stable, secret-free stack failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FixturePlan:
    repo_root: Path
    source: Path
    value: Mapping[str, Any]

    @classmethod
    def load(
        cls,
        source: Path = DEFAULT_PLAN,
        *,
        repo_root: Path = ROOT,
    ) -> "FixturePlan":
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StackFailure("stalwart_fixture_plan_unreadable") from exc
        plan = cls(repo_root=repo_root.resolve(), source=source.resolve(), value=value)
        plan.validate()
        return plan

    @property
    def domain(self) -> str:
        return str(self.value["domain"])

    @property
    def server_hostname(self) -> str:
        return str(self.value["server_hostname"])

    @property
    def accounts(self) -> list[Mapping[str, Any]]:
        return list(self.value["accounts"])

    def path(self, relative: str) -> Path:
        candidate = (self.repo_root / relative).resolve()
        try:
            candidate.relative_to(self.repo_root)
        except ValueError as exc:
            raise StackFailure("stalwart_fixture_path_escapes_repository") from exc
        return candidate

    def validate(self) -> None:
        value = self.value
        if value.get("version") != 1:
            raise StackFailure("stalwart_fixture_version_unsupported")
        if not value.get("domain") or not value.get("server_hostname"):
            raise StackFailure("stalwart_fixture_identity_missing")
        accounts = value.get("accounts")
        if not isinstance(accounts, list) or {item.get("name") for item in accounts} != {
            "alice",
            "bob",
        }:
            raise StackFailure("stalwart_fixture_accounts_invalid")
        seen_fixture_ids: set[str] = set()
        for relative in [value.get("admin_secret_file")]:
            self._validate_path(relative, secret=True)
        for account in accounts:
            self._validate_path(account.get("secret_file"), secret=True)
            if not isinstance(account.get("mailboxes"), list):
                raise StackFailure("stalwart_fixture_mailboxes_invalid")
            for message in account.get("messages", []):
                fixture_id = message.get("fixture_id")
                if not fixture_id or fixture_id in seen_fixture_ids:
                    raise StackFailure("stalwart_fixture_message_id_invalid")
                seen_fixture_ids.add(str(fixture_id))
                path = self._validate_path(message.get("path"), secret=False)
                parsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
                if not parsed.get("Message-ID") or not parsed.get("Subject"):
                    raise StackFailure("stalwart_fixture_message_headers_invalid")
                if message.get("has_attachment") and not any(
                    part.get_content_disposition() == "attachment"
                    for part in parsed.walk()
                ):
                    raise StackFailure("stalwart_fixture_attachment_missing")
                if not message.get("mailboxes") or not isinstance(
                    message.get("keywords"), list
                ):
                    raise StackFailure("stalwart_fixture_message_metadata_invalid")

    def _validate_path(self, relative: Any, *, secret: bool) -> Path:
        if not isinstance(relative, str) or not relative:
            raise StackFailure("stalwart_fixture_path_invalid")
        path = self.path(relative)
        if not path.is_file():
            raise StackFailure("stalwart_fixture_file_missing")
        if secret:
            allowed = (self.repo_root / "tests/fixtures/jmap/secrets").resolve()
            try:
                path.relative_to(allowed)
            except ValueError as exc:
                raise StackFailure("stalwart_fixture_secret_location_invalid") from exc
        return path

    def contract_summary(self) -> dict[str, Any]:
        messages = [
            message
            for account in self.accounts
            for message in account.get("messages", [])
        ]
        groups: dict[str, int] = {}
        for message in messages:
            group = message.get("thread_group")
            if group:
                groups[str(group)] = groups.get(str(group), 0) + 1
        return {
            "version": 1,
            "account_count": len(self.accounts),
            "mailbox_count": sum(len(a.get("mailboxes", [])) for a in self.accounts),
            "message_count": len(messages),
            "thread_groups": groups,
            "attachment_count": sum(
                1 for message in messages if message.get("has_attachment")
            ),
        }


@dataclass(frozen=True)
class FixtureSecrets:
    admin: str
    accounts: Mapping[str, str]

    @classmethod
    def load(cls, plan: FixturePlan) -> "FixtureSecrets":
        admin = cls._read(plan.path(str(plan.value["admin_secret_file"])))
        accounts = {
            str(account["name"]): cls._read(
                plan.path(str(account["secret_file"]))
            )
            for account in plan.accounts
        }
        return cls(admin=admin, accounts=accounts)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise StackFailure("stalwart_fixture_secret_unreadable") from exc
        if not value or "\n" in value or "\r" in value:
            raise StackFailure("stalwart_fixture_secret_invalid")
        return value


class JmapTransport(Protocol):
    def session(self, base_url: str, username: str, password: str) -> Mapping[str, Any]:
        ...

    def call(
        self,
        url: str,
        username: str,
        password: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def upload(
        self,
        url: str,
        username: str,
        password: str,
        content: bytes,
        content_type: str,
    ) -> Mapping[str, Any]:
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class HttpJmapTransport:
    """Bounded JMAP HTTP transport that never follows redirects."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))
        self._opener = urllib.request.build_opener(_NoRedirect())

    def session(self, base_url: str, username: str, password: str) -> Mapping[str, Any]:
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", ".well-known/jmap")
        return self._request(url, username, password, method="GET")

    def call(
        self,
        url: str,
        username: str,
        password: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self._request(
            url,
            username,
            password,
            method="POST",
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
        )

    def upload(
        self,
        url: str,
        username: str,
        password: str,
        content: bytes,
        content_type: str,
    ) -> Mapping[str, Any]:
        return self._request(
            url,
            username,
            password,
            method="POST",
            body=content,
            content_type=content_type,
        )

    def _request(
        self,
        url: str,
        username: str,
        password: str,
        *,
        method: str,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Mapping[str, Any]:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise StackFailure("stalwart_jmap_url_invalid")
        credential = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {credential}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=self._timeout_seconds)
            with response:
                content = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise StackFailure(f"stalwart_http_status_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StackFailure("stalwart_http_unavailable") from exc
        if len(content) > MAX_HTTP_RESPONSE_BYTES:
            raise StackFailure("stalwart_http_response_too_large")
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StackFailure("stalwart_http_response_invalid") from exc
        if not isinstance(value, dict):
            raise StackFailure("stalwart_http_response_invalid")
        return value


class StalwartFixtureSeeder:
    """Provision and verify the deterministic fixture through JMAP only."""

    def __init__(
        self,
        plan: FixturePlan,
        secrets: FixtureSecrets,
        transport: JmapTransport,
        *,
        base_url: str = "http://127.0.0.1:18080",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._plan = plan
        self._secrets = secrets
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep

    def seed(self) -> dict[str, Any]:
        self._bootstrap()
        admin_session = self._wait_for_session("admin", self._secrets.admin)
        api_url = self._absolute(str(admin_session["apiUrl"]))
        domain_id = self._ensure_domain(api_url)
        for account in self._plan.accounts:
            self._ensure_account(api_url, domain_id, account)
        self._invalidate_caches(api_url)
        for account in self._plan.accounts:
            self._seed_account(account)
        return self.verify()

    def verify(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "account_count": 0,
            "mailboxes": {},
            "message_counts": {},
            "thread_groups": {},
            "attachment_count": 0,
        }
        for account in self._plan.accounts:
            name = str(account["name"])
            session = self._wait_for_session(name, self._secrets.accounts[name])
            account_id = self._mail_account_id(session)
            api_url = self._absolute(str(session["apiUrl"]))
            mailboxes = self._mailboxes(api_url, name, account_id)
            expected_mailboxes = {"Inbox", *map(str, account.get("mailboxes", []))}
            if not expected_mailboxes.issubset(mailboxes):
                raise StackFailure("stalwart_fixture_mailbox_verification_failed")
            email_ids = self._method(
                api_url,
                name,
                self._secrets.accounts[name],
                "Email/query",
                {"accountId": account_id, "filter": {}, "limit": 100},
                "verify-email-query",
                capabilities=(CORE_CAPABILITY, MAIL_CAPABILITY),
            ).get("ids", [])
            emails = self._method(
                api_url,
                name,
                self._secrets.accounts[name],
                "Email/get",
                {
                    "accountId": account_id,
                    "ids": email_ids,
                    "properties": [
                        "id",
                        "threadId",
                        "subject",
                        "keywords",
                        "hasAttachment",
                    ],
                },
                "verify-email-get",
                capabilities=(CORE_CAPABILITY, MAIL_CAPABILITY),
            ).get("list", [])
            expected_messages = list(account.get("messages", []))
            expected_subjects = {
                self._message_subject(message) for message in expected_messages
            }
            found_by_subject = {
                str(item.get("subject")): item
                for item in emails
                if item.get("subject") in expected_subjects
            }
            if set(found_by_subject) != expected_subjects:
                raise StackFailure("stalwart_fixture_message_verification_failed")
            for message in expected_messages:
                item = found_by_subject[self._message_subject(message)]
                if not set(message.get("keywords", [])).issubset(
                    set((item.get("keywords") or {}).keys())
                ):
                    raise StackFailure("stalwart_fixture_keyword_verification_failed")
                if message.get("has_attachment") and not item.get("hasAttachment"):
                    raise StackFailure("stalwart_fixture_attachment_verification_failed")
            for group in {
                str(message["thread_group"])
                for message in expected_messages
                if message.get("thread_group")
            }:
                grouped = [
                    found_by_subject[self._message_subject(message)]
                    for message in expected_messages
                    if message.get("thread_group") == group
                ]
                thread_ids = {item.get("threadId") for item in grouped}
                if len(thread_ids) != 1 or None in thread_ids:
                    raise StackFailure("stalwart_fixture_thread_verification_failed")
                result["thread_groups"][group] = len(grouped)
            result["account_count"] += 1
            result["mailboxes"][name] = sorted(expected_mailboxes)
            result["message_counts"][name] = len(expected_messages)
            result["attachment_count"] += sum(
                1 for message in expected_messages if message.get("has_attachment")
            )
        return result

    def _bootstrap(self) -> None:
        payload = {
            "using": [CORE_CAPABILITY, MANAGEMENT_CAPABILITY],
            "methodCalls": [
                [
                    "x:Bootstrap/set",
                    {
                        "update": {
                            "singleton": {
                                "serverHostname": self._plan.server_hostname,
                                "defaultDomain": self._plan.domain,
                                "requestTlsCertificate": False,
                                "generateDkimKeys": False,
                            }
                        }
                    },
                    "bootstrap",
                ]
            ],
        }
        last_error: StackFailure | None = None
        for path in ("/jmap", "/api"):
            try:
                response = self._transport.call(
                    self._base_url + path,
                    "admin",
                    self._secrets.admin,
                    payload,
                )
                self._response(response, "x:Bootstrap/set", "bootstrap")
                return
            except StackFailure as exc:
                last_error = exc
                if exc.code not in {
                    "stalwart_http_status_404",
                    "stalwart_http_status_405",
                }:
                    raise
        raise StackFailure("stalwart_bootstrap_endpoint_missing") from last_error

    def _ensure_domain(self, api_url: str) -> str:
        found = self._method(
            api_url,
            "admin",
            self._secrets.admin,
            "x:Domain/query",
            {"filter": {"name": self._plan.domain}, "limit": 2},
            "domain-query",
        ).get("ids", [])
        if found:
            return str(found[0])
        created = self._method(
            api_url,
            "admin",
            self._secrets.admin,
            "x:Domain/set",
            {
                "create": {
                    "fixture-domain": {
                        "name": self._plan.domain,
                        "description": "Ananta deterministic JMAP fixture",
                        "isEnabled": True,
                    }
                }
            },
            "domain-create",
        ).get("created", {})
        try:
            return str(created["fixture-domain"]["id"])
        except (KeyError, TypeError) as exc:
            raise StackFailure("stalwart_fixture_domain_create_failed") from exc

    def _ensure_account(
        self,
        api_url: str,
        domain_id: str,
        account: Mapping[str, Any],
    ) -> str:
        name = str(account["name"])
        found = self._method(
            api_url,
            "admin",
            self._secrets.admin,
            "x:Account/query",
            {"filter": {"name": name, "domainId": domain_id}, "limit": 2},
            f"account-query-{name}",
        ).get("ids", [])
        if found:
            return str(found[0])
        created = self._method(
            api_url,
            "admin",
            self._secrets.admin,
            "x:Account/set",
            {
                "create": {
                    f"fixture-{name}": {
                        "@type": "User",
                        "name": name,
                        "domainId": domain_id,
                        "description": f"Ananta fixture account {name}",
                        "credentials": {
                            "password": {
                                "@type": "Password",
                                "secret": self._secrets.accounts[name],
                            }
                        },
                        "memberGroupIds": {},
                        "roles": {"@type": "User"},
                        "permissions": {"@type": "Inherit"},
                        "quotas": {},
                        "aliases": {},
                        "encryptionAtRest": {"@type": "Disabled"},
                    }
                }
            },
            f"account-create-{name}",
        ).get("created", {})
        try:
            return str(created[f"fixture-{name}"]["id"])
        except (KeyError, TypeError) as exc:
            raise StackFailure("stalwart_fixture_account_create_failed") from exc

    def _invalidate_caches(self, api_url: str) -> None:
        self._method(
            api_url,
            "admin",
            self._secrets.admin,
            "x:Action/set",
            {"create": {"invalidate": {"@type": "InvalidateCaches"}}},
            "invalidate-caches",
        )

    def _seed_account(self, account: Mapping[str, Any]) -> None:
        name = str(account["name"])
        password = self._secrets.accounts[name]
        session = self._wait_for_session(name, password)
        account_id = self._mail_account_id(session)
        api_url = self._absolute(str(session["apiUrl"]))
        mailboxes = self._mailboxes(api_url, name, account_id)
        for mailbox_name in account.get("mailboxes", []):
            mailbox_name = str(mailbox_name)
            if mailbox_name in mailboxes:
                continue
            created = self._method(
                api_url,
                name,
                password,
                "Mailbox/set",
                {
                    "accountId": account_id,
                    "create": {
                        f"mailbox-{mailbox_name}": {
                            "name": mailbox_name,
                            "parentId": None,
                            "role": None,
                            "sortOrder": 100,
                        }
                    },
                },
                f"mailbox-create-{mailbox_name}",
                capabilities=(CORE_CAPABILITY, MAIL_CAPABILITY),
            ).get("created", {})
            try:
                mailboxes[mailbox_name] = str(
                    created[f"mailbox-{mailbox_name}"]["id"]
                )
            except (KeyError, TypeError) as exc:
                raise StackFailure("stalwart_fixture_mailbox_create_failed") from exc
        upload_url = self._template_url(str(session["uploadUrl"]), account_id)
        imports: dict[str, Any] = {}
        for message in account.get("messages", []):
            content = self._plan.path(str(message["path"])).read_bytes()
            uploaded = self._transport.upload(
                upload_url,
                name,
                password,
                content,
                "message/rfc822",
            )
            blob_id = uploaded.get("blobId")
            if not blob_id:
                raise StackFailure("stalwart_fixture_upload_failed")
            try:
                mailbox_ids = {
                    mailboxes[str(mailbox)]: True
                    for mailbox in message.get("mailboxes", [])
                }
            except KeyError as exc:
                raise StackFailure("stalwart_fixture_mailbox_reference_invalid") from exc
            imports[str(message["fixture_id"])] = {
                "blobId": str(blob_id),
                "mailboxIds": mailbox_ids,
                "keywords": {
                    str(keyword): True for keyword in message.get("keywords", [])
                },
                "receivedAt": str(message["received_at"]),
            }
        if imports:
            response = self._method(
                api_url,
                name,
                password,
                "Email/import",
                {"accountId": account_id, "emails": imports},
                "email-import",
                capabilities=(CORE_CAPABILITY, MAIL_CAPABILITY),
            )
            if response.get("notCreated"):
                raise StackFailure("stalwart_fixture_email_import_failed")

    def _mailboxes(
        self,
        api_url: str,
        username: str,
        account_id: str,
    ) -> dict[str, str]:
        response = self._method(
            api_url,
            username,
            self._secrets.accounts[username],
            "Mailbox/get",
            {"accountId": account_id, "ids": None},
            "mailbox-get",
            capabilities=(CORE_CAPABILITY, MAIL_CAPABILITY),
        )
        result = {
            str(item["name"]): str(item["id"])
            for item in response.get("list", [])
            if item.get("name") and item.get("id")
        }
        for item in response.get("list", []):
            if item.get("role") == "inbox" and item.get("id"):
                result["Inbox"] = str(item["id"])
        return result

    def _wait_for_session(self, username: str, password: str) -> Mapping[str, Any]:
        last_error: StackFailure | None = None
        for _ in range(30):
            try:
                session = self._transport.session(self._base_url, username, password)
                if session.get("apiUrl") and session.get("accounts") is not None:
                    return session
            except StackFailure as exc:
                last_error = exc
            self._sleep(1.0)
        raise StackFailure("stalwart_jmap_session_timeout") from last_error

    def _mail_account_id(self, session: Mapping[str, Any]) -> str:
        for account_id, account in (session.get("accounts") or {}).items():
            capabilities = account.get("accountCapabilities") or {}
            if MAIL_CAPABILITY in capabilities:
                return str(account_id)
        raise StackFailure("stalwart_jmap_mail_account_missing")

    def _method(
        self,
        url: str,
        username: str,
        password: str,
        method: str,
        arguments: Mapping[str, Any],
        call_id: str,
        *,
        capabilities: Sequence[str] = (CORE_CAPABILITY, MANAGEMENT_CAPABILITY),
    ) -> Mapping[str, Any]:
        response = self._transport.call(
            self._absolute(url),
            username,
            password,
            {"using": list(capabilities), "methodCalls": [[method, arguments, call_id]]},
        )
        return self._response(response, method, call_id)

    @staticmethod
    def _response(
        response: Mapping[str, Any],
        expected_method: str,
        call_id: str,
    ) -> Mapping[str, Any]:
        for item in response.get("methodResponses", []):
            if not isinstance(item, list) or len(item) != 3 or item[2] != call_id:
                continue
            if item[0] == "error":
                raise StackFailure("stalwart_jmap_method_failed")
            if item[0] != expected_method or not isinstance(item[1], dict):
                raise StackFailure("stalwart_jmap_method_response_invalid")
            if item[1].get("notCreated") or item[1].get("notUpdated"):
                raise StackFailure("stalwart_jmap_set_failed")
            return item[1]
        raise StackFailure("stalwart_jmap_method_response_missing")

    def _absolute(self, url: str) -> str:
        return urllib.parse.urljoin(self._base_url.rstrip("/") + "/", url)

    def _template_url(self, url: str, account_id: str) -> str:
        quoted = urllib.parse.quote(account_id, safe="")
        return self._absolute(
            url.replace("{accountId}", quoted).replace("%7BaccountId%7D", quoted)
        )

    def _message_subject(self, message: Mapping[str, Any]) -> str:
        parsed = BytesParser(policy=policy.default).parsebytes(
            self._plan.path(str(message["path"])).read_bytes()
        )
        return str(parsed["Subject"])


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        ...


class SubprocessCommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(argv),
                cwd=ROOT,
                env=dict(env),
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StackFailure("stalwart_stack_command_unavailable") from exc


class ComposeStack:
    """Own only the Docker Compose lifecycle and zero-resource assertion."""

    project_name = "ananta-jmap-test"

    def __init__(
        self,
        admin_password: str,
        *,
        runner: CommandRunner | None = None,
        repo_root: Path = ROOT,
    ) -> None:
        self._admin_password = admin_password
        self._runner = runner or SubprocessCommandRunner()
        self._repo_root = repo_root
        self._compose = [
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "-f",
            str(repo_root / "docker/compose-next/compose.jmap-test.yml"),
            "-f",
            str(repo_root / "docker/compose-next/compose.jmap-seed.yml"),
            "--profile",
            "jmap-test",
        ]

    @staticmethod
    def require_live_ack(environment: Mapping[str, str] | None = None) -> None:
        environment = environment or os.environ
        if environment.get(LIVE_ACK_ENV) != "1":
            raise StackFailure("stalwart_live_license_ack_required")

    def up(self) -> None:
        self.require_live_ack()
        self._checked(
            [
                *self._compose,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "90",
            ],
            "stalwart_stack_up_failed",
        )

    def down(self) -> None:
        self._checked(
            [
                *self._compose,
                "down",
                "--volumes",
                "--remove-orphans",
                "--timeout",
                "10",
            ],
            "stalwart_stack_down_failed",
        )
        self.assert_zero_resources()

    def reset(self) -> None:
        self.down()
        self.up()

    def assert_zero_resources(self) -> None:
        label = f"com.docker.compose.project={self.project_name}"
        checks = (
            ["docker", "ps", "-aq", "--filter", f"label={label}"],
            ["docker", "volume", "ls", "-q", "--filter", f"label={label}"],
            ["docker", "network", "ls", "-q", "--filter", f"label={label}"],
        )
        for command in checks:
            result = self._run(command)
            if result.returncode != 0:
                raise StackFailure("stalwart_stack_resource_check_failed")
            if result.stdout.strip():
                raise StackFailure("stalwart_stack_resources_remaining")

    def _checked(self, command: Sequence[str], failure: str) -> None:
        result = self._run(command)
        if result.returncode != 0:
            raise StackFailure(failure)

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["STALWART_TEST_ADMIN_PASSWORD"] = self._admin_password
        return self._runner.run(command, env=environment)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("contract", "up", "seed", "verify", "reset", "down"),
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        plan = FixturePlan.load(args.plan)
        secrets = FixtureSecrets.load(plan)
        if args.command == "contract":
            print(json.dumps(plan.contract_summary(), sort_keys=True))
            return 0
        stack = ComposeStack(secrets.admin)
        if args.command == "down":
            stack.down()
            print('{"resources":0}')
            return 0
        ComposeStack.require_live_ack()
        seeder = StalwartFixtureSeeder(
            plan,
            secrets,
            HttpJmapTransport(),
            base_url=args.base_url,
        )
        if args.command == "up":
            stack.up()
            result = seeder.seed()
        elif args.command == "reset":
            stack.reset()
            result = seeder.seed()
        elif args.command == "seed":
            result = seeder.seed()
        else:
            result = seeder.verify()
        print(json.dumps(result, sort_keys=True))
        return 0
    except StackFailure as exc:
        print(exc.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
