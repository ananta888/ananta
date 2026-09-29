from __future__ import annotations

from agent.runtime_policy import resolve_cli_backend


def test_research_auto_routes_to_browser_use_when_configured():
    backend, reason, _cfg = resolve_cli_backend(
        task_kind="research",
        requested_backend="auto",
        supported_backends={"ananta-worker", "browser_use"},
        agent_cfg={
            "research_backend": {
                "provider": "browser_use",
                "enabled": True,
                "mode": "native",
                "providers": {
                    "browser_use": {
                        "enabled": True,
                        "mode": "native",
                        "command": "bash -lc true",
                    }
                },
            }
        },
    )
    assert backend == "browser_use"
    assert reason == "research_backend_policy:research->browser_use"


def test_research_auto_falls_back_to_default_when_browser_not_supported():
    backend, reason, _cfg = resolve_cli_backend(
        task_kind="research",
        requested_backend="auto",
        supported_backends={"ananta-worker"},
        agent_cfg={
            "research_backend": {
                "provider": "browser_use",
                "enabled": True,
            },
            "sgpt_routing": {"default_backend": "ananta-worker"},
        },
    )
    assert backend == "ananta-worker"
    assert reason.startswith("default_policy:")
