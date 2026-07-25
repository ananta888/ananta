from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from scripts.stalwart_jmap_test_stack import (
    CORE_CAPABILITY,
    MAIL_CAPABILITY,
    ComposeStack,
    FixturePlan,
    FixtureSecrets,
    StackFailure,
    StalwartFixtureSeeder,
)


ROOT = Path(__file__).resolve().parents[2]


class InProcessStalwart:
    def __init__(self) -> None:
        self.bootstrapped = False
        self.domain_id: str | None = None
        self.accounts: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.emails: dict[str, list[dict[str, Any]]] = {}
        self.thread_roots: dict[str, str] = {}

    def session(self, base_url: str, username: str, password: str) -> Mapping[str, Any]:
        if username == "admin":
            if not self.bootstrapped:
                raise StackFailure("stalwart_http_unavailable")
            return {
                "apiUrl": "/jmap",
                "uploadUrl": "/jmap/upload/{accountId}",
                "accounts": {},
            }
        account = self.accounts.get(username)
        if not account or account["password"] != password:
            raise StackFailure("stalwart_http_status_401")
        account_id = account["id"]
        return {
            "apiUrl": "/jmap",
            "uploadUrl": "/jmap/upload/{accountId}",
            "accounts": {
                account_id: {
                    "isPersonal": True,
                    "accountCapabilities": {MAIL_CAPABILITY: {}},
                }
            },
        }

    def upload(
        self,
        url: str,
        username: str,
        password: str,
        content: bytes,
        content_type: str,
    ) -> Mapping[str, Any]:
        assert content_type == "message/rfc822"
        assert self.accounts[username]["password"] == password
        blob_id = f"blob-{len(self.blobs) + 1}"
        self.blobs[blob_id] = content
        return {"accountId": self.accounts[username]["id"], "blobId": blob_id}

    def call(
        self,
        url: str,
        username: str,
        password: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        method, arguments, call_id = payload["methodCalls"][0]
        result = self._dispatch(username, method, arguments)
        return {"methodResponses": [[method, result, call_id]]}

    def _dispatch(
        self,
        username: str,
        method: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if method == "x:Bootstrap/set":
            assert arguments["update"]["singleton"]["requestTlsCertificate"] is False
            self.bootstrapped = True
            return {"updated": {"singleton": None}}
        if method == "x:Domain/query":
            return {"ids": [self.domain_id] if self.domain_id else []}
        if method == "x:Domain/set":
            self.domain_id = "domain-1"
            return {"created": {"fixture-domain": {"id": self.domain_id}}}
        if method == "x:Account/query":
            name = arguments["filter"]["name"]
            return {"ids": [self.accounts[name]["id"]] if name in self.accounts else []}
        if method == "x:Account/set":
            created: dict[str, Any] = {}
            for reference, value in arguments["create"].items():
                name = value["name"]
                account_id = f"account-{name}"
                self.accounts[name] = {
                    "id": account_id,
                    "password": value["credentials"]["password"]["secret"],
                    "mailboxes": {
                        "Inbox": {
                            "id": f"mailbox-{name}-inbox",
                            "name": "Inbox",
                            "role": "inbox",
                        }
                    },
                }
                self.emails[name] = []
                created[reference] = {"id": account_id}
            return {"created": created}
        if method == "x:Action/set":
            return {"created": {"invalidate": {"id": "action-1"}}}
        if method == "Mailbox/get":
            return {"list": list(self.accounts[username]["mailboxes"].values())}
        if method == "Mailbox/set":
            created = {}
            for reference, value in arguments["create"].items():
                mailbox_id = f"mailbox-{username}-{len(self.accounts[username]['mailboxes'])}"
                item = {"id": mailbox_id, **value}
                self.accounts[username]["mailboxes"][value["name"]] = item
                created[reference] = {"id": mailbox_id}
            return {"created": created}
        if method == "Email/import":
            created = {}
            for reference, value in arguments["emails"].items():
                parsed = BytesParser(policy=policy.default).parsebytes(
                    self.blobs[value["blobId"]]
                )
                message_id = str(parsed["Message-ID"])
                parent = str(parsed["In-Reply-To"] or "")
                root = parent or message_id
                thread_id = self.thread_roots.setdefault(root, f"thread-{len(self.thread_roots) + 1}")
                if parent:
                    self.thread_roots[message_id] = thread_id
                item = {
                    "id": f"email-{username}-{len(self.emails[username]) + 1}",
                    "threadId": thread_id,
                    "subject": str(parsed["Subject"]),
                    "keywords": value["keywords"],
                    "hasAttachment": any(
                        part.get_content_disposition() == "attachment"
                        for part in parsed.walk()
                    ),
                }
                self.emails[username].append(item)
                created[reference] = {"id": item["id"]}
            return {"created": created}
        if method == "Email/query":
            return {"ids": [item["id"] for item in self.emails[username]]}
        if method == "Email/get":
            ids = set(arguments["ids"])
            return {"list": [item for item in self.emails[username] if item["id"] in ids]}
        raise AssertionError(f"unexpected method: {method}")


class FakeCommandRunner:
    def __init__(self, outputs: Sequence[str] = ()) -> None:
        self.outputs = list(outputs)
        self.commands: list[list[str]] = []
        self.environments: list[Mapping[str, str]] = []

    def run(self, argv: Sequence[str], *, env: Mapping[str, str]) -> Any:
        self.commands.append(list(argv))
        self.environments.append(env)
        output = self.outputs.pop(0) if self.outputs else ""
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()


def test_fixture_contract_bootstraps_seeds_and_verifies_in_process(
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = FixturePlan.load()
    secrets = FixtureSecrets.load(plan)
    server = InProcessStalwart()

    result = StalwartFixtureSeeder(
        plan,
        secrets,
        server,
        sleep=lambda _: None,
    ).seed()

    assert result == {
        "account_count": 2,
        "attachment_count": 1,
        "mailboxes": {
            "alice": ["Inbox", "Projects"],
            "bob": ["Archive", "Inbox"],
        },
        "message_counts": {"alice": 2, "bob": 1},
        "thread_groups": {"lighthouse": 2},
    }
    output = capsys.readouterr()
    serialized_output = output.out + output.err
    assert secrets.admin not in serialized_output
    assert all(secret not in serialized_output for secret in secrets.accounts.values())


def test_fixture_plan_is_deterministic_and_complete() -> None:
    plan = FixturePlan.load()

    assert plan.contract_summary() == {
        "version": 1,
        "account_count": 2,
        "mailbox_count": 2,
        "message_count": 3,
        "thread_groups": {"lighthouse": 2},
        "attachment_count": 1,
    }
    assert json.loads(plan.source.read_text(encoding="utf-8")) == plan.value


def test_live_stack_requires_explicit_license_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANANTA_STALWART_TEST_LICENSE_ACK", raising=False)

    with pytest.raises(StackFailure, match="stalwart_live_license_ack_required"):
        ComposeStack.require_live_ack()


def test_teardown_removes_and_checks_all_project_resources() -> None:
    runner = FakeCommandRunner()
    stack = ComposeStack(
        "test-only",
        runner=runner,
        repo_root=ROOT,
    )

    stack.down()

    assert "down" in runner.commands[0]
    assert "--volumes" in runner.commands[0]
    assert "--remove-orphans" in runner.commands[0]
    assert [command[1:3] for command in runner.commands[1:]] == [
        ["ps", "-aq"],
        ["volume", "ls"],
        ["network", "ls"],
    ]
    assert all(
        environment["STALWART_TEST_ADMIN_PASSWORD"] == "test-only"
        for environment in runner.environments
    )


def test_teardown_fails_if_a_project_resource_remains() -> None:
    runner = FakeCommandRunner(outputs=("", "container-id"))
    stack = ComposeStack("test-only", runner=runner, repo_root=ROOT)

    with pytest.raises(StackFailure, match="stalwart_stack_resources_remaining"):
        stack.down()
