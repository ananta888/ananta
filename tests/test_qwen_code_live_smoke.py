from types import SimpleNamespace

from agent.cli_backends.coding_agent_contract import CodingAgentRunResult, ProviderState
from scripts.run_qwen_code_live_smoke import run_smoke


class FakeQwenProvider:
    def __init__(self, *, create_expected_artifact: bool = True) -> None:
        self.create_expected_artifact = create_expected_artifact
        self.requests = []

    def detect(self):
        return SimpleNamespace(state=ProviderState.READY, reason_code="ready")

    def run(self, request, *, event_sink=None):
        self.requests.append(request)
        self.saw_git_repository = (request.workspace / ".git").is_dir()
        if self.create_expected_artifact:
            (request.workspace / "qwen-smoke-result.txt").write_text(
                "ananta-qwen-headless-smoke-ok\n",
                encoding="utf-8",
            )
        if event_sink:
            event_sink(SimpleNamespace(stream="stdout", text="done"))
        return CodingAgentRunResult("qwen_code", 0, "done", "", "completed", 7)


def test_live_smoke_skips_without_explicit_machine_authorization() -> None:
    called = False

    def provider_factory():
        nonlocal called
        called = True
        return FakeQwenProvider()

    return_code, payload = run_smoke({}, provider_factory=provider_factory)

    assert return_code == 0
    assert payload["status"] == "skipped"
    assert payload["reason_code"] == "live_smoke_not_authorized"
    assert payload["interactive_input_required"] is False
    assert called is False


def test_live_smoke_fails_closed_when_authorized_without_auth() -> None:
    return_code, payload = run_smoke({"ANANTA_QWEN_LIVE_SMOKE": "1"})

    assert return_code == 2
    assert payload["status"] == "failed"
    assert payload["reason_code"] == "qwen_live_auth_not_configured"


def test_live_smoke_runs_isolated_repository_task_headlessly() -> None:
    provider = FakeQwenProvider()

    return_code, payload = run_smoke(
        {
            "ANANTA_QWEN_LIVE_SMOKE": "1",
            "BAILIAN_CODING_PLAN_API_KEY": "configured-by-ci",
            "ANANTA_QWEN_LIVE_SMOKE_TIMEOUT_SECONDS": "45",
        },
        provider_factory=lambda: provider,
    )

    assert return_code == 0
    assert payload["status"] == "passed"
    assert payload["reason_code"] == "qwen_live_smoke_passed"
    assert payload["event_count"] == 1
    assert provider.requests[0].timeout_seconds == 45
    assert provider.requests[0].permission_mode == "workspace_write"
    assert provider.saw_git_repository is True


def test_live_smoke_rejects_success_without_expected_repository_artifact() -> None:
    return_code, payload = run_smoke(
        {"ANANTA_QWEN_LIVE_SMOKE": "1", "DASHSCOPE_API_KEY": "configured-by-ci"},
        provider_factory=lambda: FakeQwenProvider(create_expected_artifact=False),
    )

    assert return_code == 1
    assert payload["reason_code"] == "qwen_live_artifact_mismatch"
