from agent.services._task_scoped_runtime import routing_dimensions


def test_aider_routing_dimensions_report_client_independent_inference_target() -> None:
    dimensions = routing_dimensions(
        backend_used="aider",
        requested_backend="aider",
        model=None,
        agent_cfg={
            "default_provider": "openai",
            "aider_cli": {"target_provider": "local_coder", "model": "qwen3-coder"},
            "local_openai_backends": [
                {"id": "local_coder", "base_url": "http://127.0.0.1:9000/v1"}
            ],
        },
    )

    assert dimensions["execution_backend"] == "aider"
    assert dimensions["inference_provider"] == "local_coder"
    assert dimensions["inference_model"] == "qwen3-coder"
    assert dimensions["inference_base_url"] == "http://127.0.0.1:9000/v1"
    assert dimensions["inference_target_kind"] == "local_openai"


def test_qwen_routing_dimensions_do_not_invent_cli_account_provider() -> None:
    dimensions = routing_dimensions(
        backend_used="qwen_code",
        requested_backend="qwen_code",
        model="qwen3-coder",
        agent_cfg={"default_provider": "ollama"},
    )

    assert dimensions["execution_backend"] == "qwen_code"
    assert dimensions["inference_provider"] is None
    assert dimensions["inference_model"] == "qwen3-coder"
