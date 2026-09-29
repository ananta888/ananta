from types import SimpleNamespace

from agent.cli_backends.account_login import CliBackendAccountLoginService


class FakeBridge:
    def __init__(self, output: str):
        self.process = SimpleNamespace(poll=lambda: None)
        self._chunks = [output]
        self.writes: list[str] = []
        self.closed = False

    def start(self):
        return None

    def wait_for_output(self, _timeout: float):
        return bool(self._chunks)

    def drain(self):
        chunks, self._chunks = self._chunks, []
        return chunks

    def write(self, value: str):
        self.writes.append(value)

    def close(self):
        self.closed = True


def test_codex_login_exposes_device_url_and_code_without_shell():
    bridge = FakeBridge(
        "Open https://auth.openai.com/codex/device\n"
        "Enter this one-time code\nABCD-EFGHI\n"
    )
    calls = []

    def factory(shell, *, argv):
        calls.append((shell, argv))
        return bridge

    service = CliBackendAccountLoginService(
        bridge_factory=factory,
        binary_resolver=lambda _backend: "/opt/codex",
    )

    result = service.start("codex")

    assert calls == [
        ("/opt/codex", ["/opt/codex", "login", "--device-auth"])
    ]
    assert result["status"] == "pending"
    assert result["verification_url"] == "https://auth.openai.com/codex/device"
    assert result["user_code"] == "ABCD-EFGHI"


def test_claude_login_accepts_browser_return_code():
    bridge = FakeBridge(
        "If the browser didn't open, visit: "
        "https://claude.com/cai/oauth/authorize?state=opaque\n"
        "Paste code here if prompted > "
    )
    service = CliBackendAccountLoginService(
        bridge_factory=lambda _shell, *, argv: bridge,
        binary_resolver=lambda _backend: "/opt/claude",
    )
    started = service.start("claude_code")

    result = service.submit_input(
        "claude_code", started["session_id"], "browser-return-code"
    )

    assert result["requires_input"] is True
    assert bridge.writes == ["browser-return-code\n"]


def test_completed_login_is_reported_as_authenticated():
    bridge = FakeBridge("https://auth.openai.com/codex/device\nABCD-EFGHI\n")
    process = SimpleNamespace(returncode=None)
    process.poll = lambda: process.returncode
    bridge.process = process
    service = CliBackendAccountLoginService(
        bridge_factory=lambda _shell, *, argv: bridge,
        binary_resolver=lambda _backend: "/opt/codex",
    )
    started = service.start("codex")
    process.returncode = 0

    result = service.status("codex", started["session_id"])

    assert result["authenticated"] is True
    assert result["status"] == "authenticated"
