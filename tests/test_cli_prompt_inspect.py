"""CLI contract tests for prompt inspection commands. PTI-030."""
from __future__ import annotations

import json
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest


class TestPromptHelp:
    def test_prompt_help_exits_zero(self):
        from agent.cli.main import main
        code = main(["prompt"])
        assert code == 0

    def test_llm_log_help_exits_zero(self):
        from agent.cli.main import main
        code = main(["llm-log"])
        assert code == 0

    def test_unknown_command_exits_nonzero(self):
        from agent.cli.main import main
        code = main(["prompt", "nonexistent-subcommand"])
        assert code != 0

    def test_task_inspect_top_level_dispatch(self):
        from agent.cli.main import main

        seen = {}

        def _run_prompt_command(args):
            seen["prompt_cmd"] = getattr(args, "prompt_cmd", None)
            seen["task_id"] = getattr(args, "task_id", None)
            return 0

        with patch("agent.cli.prompt_inspect.run_prompt_command", side_effect=_run_prompt_command):
            code = main(["task", "inspect", "--task-id", "task-123"])

        assert code == 0
        assert seen["prompt_cmd"] == "task-inspect"
        assert seen["task_id"] == "task-123"


class TestLLMLogTail:
    def test_tail_no_log_file_exits_zero(self, tmp_path):
        from agent.cli.prompt_inspect import cmd_llm_log_tail
        import argparse

        args = argparse.Namespace(limit=5, provider=None, model=None, goal_id=None, task_id=None, json=False)

        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
            with patch("agent.utils.get_data_dir", return_value=str(tmp_path)):
                code = cmd_llm_log_tail(args)
        assert code == 0

    def test_tail_json_output_valid(self, tmp_path):
        import argparse, io, contextlib
        from agent.cli.prompt_inspect import cmd_llm_log_tail
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage

        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)
        trace = svc.create_trace(provider="ollama", prompt="test tail")
        svc.store(svc.finalize_trace(trace, success=True))

        args = argparse.Namespace(limit=5, provider=None, model=None, goal_id=None, task_id=None, json=True)

        captured = io.StringIO()
        with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
            with contextlib.redirect_stdout(captured):
                code = cmd_llm_log_tail(args)

        assert code == 0
        output = captured.getvalue()
        parsed = json.loads(output)
        assert isinstance(parsed, list)

    def test_tail_provider_filter(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage

        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        for provider in ("lmstudio", "ollama"):
            t = svc.create_trace(provider=provider, prompt=f"from {provider}")
            svc.store(svc.finalize_trace(t, success=True))

        traces = svc.list_traces(limit=10, provider="lmstudio")
        assert all(t.provider == "lmstudio" for t in traces)


class TestPromptInspect:
    def test_inspect_unknown_trace_exits_one(self, tmp_path):
        import argparse
        from agent.cli.prompt_inspect import cmd_prompt_inspect
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage

        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)
        args = argparse.Namespace(trace_id="no-such-trace", json=False, full=False)

        with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
            code = cmd_prompt_inspect(args)
        assert code == 1

    def test_inspect_json_output_valid(self, tmp_path):
        import argparse, io, contextlib
        from agent.cli.prompt_inspect import cmd_prompt_inspect
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage

        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)
        trace = svc.create_trace(provider="lmstudio", prompt="inspect test")
        svc.store(svc.finalize_trace(trace, success=True))

        args = argparse.Namespace(trace_id=trace.trace_id, json=True, full=False)
        captured = io.StringIO()
        with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_inspect(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["trace_id"] == trace.trace_id


class TestPromptRender:
    def test_render_no_network(self, tmp_path):
        import argparse, io, contextlib
        from agent.cli.prompt_inspect import cmd_prompt_render
        from agent.services.planning_prompt_registry import ResolvedPlanningPrompt

        mock_resolved = ResolvedPlanningPrompt(
            prompt_version_id="v1-test",
            version="v1",
            language="de",
            mode="generic",
            prompt="Plan to build a tool",
            checksum="abc123",
        )

        args = argparse.Namespace(
            mode="generic",
            goal="Build a tool",
            language="de",
            model_family=None,
            context_file=None,
            preferred_output_format="json",
            save_trace=False,
            json=False,
        )

        captured = io.StringIO()
        with patch("agent.services.planning_prompt_registry.get_planning_prompt_registry") as mock_reg:
            mock_reg.return_value.resolve.return_value = mock_resolved
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_render(args)

        assert code == 0
        assert "v1-test" in captured.getvalue()

    def test_render_raw_denied_by_default(self):
        from agent.services.prompt_trace_access_policy import PromptTraceAccessPolicy
        policy = PromptTraceAccessPolicy()
        decision = policy.check_raw_access(is_admin=False, is_local=False, raw_available=False)
        assert not decision.allowed
        assert "raw_not_stored" in decision.reason or "disabled" in decision.reason or "denied" in decision.reason


class TestPromptDelegationReport:
    def test_delegation_report_json_happy_path(self):
        import argparse, io, contextlib
        from agent.cli.prompt_inspect import cmd_prompt_delegation_report

        class _Resp:
            def __init__(self, status_code: int):
                self.status_code = status_code

        goal_detail = {
            "goal": {"status": "planned"},
            "tasks": [
                {
                    "id": "task-1",
                    "title": "Implement",
                    "status": "proposing",
                    "task_kind": "coding",
                    "required_capabilities": ["coding"],
                    "assigned_agent_url": "http://worker-a:5000",
                    "verification_status": {"execution_scope": {"worker_url": "http://worker-a:5000"}},
                    "instruction_layers": {
                        "selected_profile": "big-pickle",
                        "selected_overlay": "safe-mode",
                        "template_compatibility": {
                            "status": "ok",
                            "role_template_context": {"template_id": "tpl-1", "template_name": "Backend Template"},
                        },
                    },
                }
            ],
        }
        prompt_traces = {
            "total": 2,
            "traces": {
                "generate": [
                    {
                        "task_id": "task-1",
                        "request_kind": "generate",
                        "provider": "lmstudio",
                        "model": "google/gemma-4-e4b",
                        "prompt_hash_sha256": "abc123",
                        "prompt_preview_redacted": "hello",
                        "created_at": 100.0,
                    }
                ],
                "repair": [
                    {
                        "task_id": "task-1",
                        "request_kind": "repair",
                        "provider": "lmstudio",
                        "model": "google/gemma-4-e4b",
                        "prompt_hash_sha256": "def456",
                        "prompt_preview_redacted": "newer",
                        "created_at": 200.0,
                    }
                ],
            },
        }

        def _request(method, path, **kwargs):
            if path.endswith("/detail"):
                return _Resp(200)
            if path.endswith("/prompt-traces"):
                return _Resp(200)
            return _Resp(404)

        def _api_data(resp):
            if resp.status_code != 200:
                return {}
            # detail request first, traces second in command flow
            if not getattr(_api_data, "_seen_detail", False):
                _api_data._seen_detail = True
                return goal_detail
            return prompt_traces

        args = argparse.Namespace(goal_id="goal-1", json=True)
        captured = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), patch("agent.cli_goals._api_data", side_effect=_api_data):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_delegation_report(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["goal_id"] == "goal-1"
        assert parsed["task_count"] == 1
        task = parsed["tasks"][0]
        assert task["task_id"] == "task-1"
        assert task["instruction_layers"]["template_compatibility"]["role_template_context"]["template_id"] == "tpl-1"
        assert task["last_prompt_trace"]["request_kind"] == "repair"
        assert task["last_prompt_trace"]["prompt_hash"] == "def456"

    def test_delegation_report_missing_optional_fields(self):
        import argparse, io, contextlib
        from agent.cli.prompt_inspect import cmd_prompt_delegation_report

        class _Resp:
            def __init__(self, status_code: int):
                self.status_code = status_code

        goal_detail = {
            "goal": {"status": "planned"},
            "tasks": [{"id": "task-2", "title": "Doc", "status": "todo"}],
        }
        prompt_traces = {"total": 0, "traces": {}}

        def _request(method, path, **kwargs):
            if path.endswith("/detail"):
                return _Resp(200)
            if path.endswith("/prompt-traces"):
                return _Resp(200)
            return _Resp(404)

        def _api_data(resp):
            if not getattr(_api_data, "_seen_detail", False):
                _api_data._seen_detail = True
                return goal_detail
            return prompt_traces

        args = argparse.Namespace(goal_id="goal-2", json=True)
        captured = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), patch("agent.cli_goals._api_data", side_effect=_api_data):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_delegation_report(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["task_count"] == 1
        task = parsed["tasks"][0]
        assert task["assigned_agent_url"] == ""
        assert task["instruction_layers"]["selected_profile"] is None
        assert task["last_prompt_trace"]["request_kind"] == ""


class TestPromptTaskReport:
    def test_task_report_json_happy_path(self, tmp_path):
        import argparse
        import contextlib
        import io
        from agent.cli.prompt_inspect import cmd_prompt_task_report
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage

        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(
            request_id="req-1",
            task_id="task-1",
            goal_id="goal-1",
            provider="lmstudio",
            model="google/gemma-4-e4b",
            request_kind="planning",
            prompt="Build a Fibonacci backend",
        )
        svc.store(svc.finalize_trace(trace, success=True, response_text='{"tasks":[{"title":"Setup"}]}'))

        llm_log_path = tmp_path / "llm_log.jsonl"
        llm_log_path.write_text(
            json.dumps(
                {
                    "timestamp": 200.0,
                    "event": "llm_call_end",
                    "request_id": "req-1",
                    "provider": "lmstudio",
                    "model": "google/gemma-4-e4b",
                    "success": True,
                    "response": '{"tasks":[{"title":"Setup"}]}',
                }
            )
            + "\n",
            encoding="utf-8",
        )

        task_detail = {
            "id": "task-1",
            "title": "Plan Fibonacci backend",
            "status": "planning",
            "task_kind": "planning",
            "assigned_agent_url": "http://worker-a:5000",
            "required_capabilities": ["planning"],
            "instruction_layers": {
                "selected_profile": "big-pickle",
                "selected_overlay": "default",
                "template_compatibility": {"status": "ok"},
            },
        }

        class _Resp:
            def __init__(self, status_code: int, payload: dict | None = None):
                self.status_code = status_code
                self._payload = payload or {}

        def _request(method, path, **kwargs):
            if path == "/tasks/task-1":
                return _Resp(200, task_detail)
            return _Resp(404)

        def _api_data(resp):
            return getattr(resp, "_payload", {})

        args = argparse.Namespace(task_id="task-1", json=True)
        captured = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), \
            patch("agent.cli_goals._api_data", side_effect=_api_data), \
            patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc), \
            patch("agent.utils.get_data_dir", return_value=str(tmp_path)):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_task_report(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["task"]["task_id"] == "task-1"
        assert parsed["trace_count"] == 1
        trace_row = parsed["traces"][0]
        assert trace_row["request_kind"] == "planning"
        assert trace_row["response_preview"].startswith('{"tasks"')
        assert trace_row["prompt_preview_redacted"].startswith("Build a Fibonacci backend")

    def test_task_report_missing_optional_fields(self, tmp_path):
        import argparse
        import contextlib
        import io
        from agent.cli.prompt_inspect import cmd_prompt_task_report
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage

        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(task_id="task-2", request_id="req-2", prompt="Doc this")
        svc.store(svc.finalize_trace(trace, success=True, response_text="Done"))

        (tmp_path / "llm_log.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": 300.0,
                    "event": "llm_call_end",
                    "request_id": "req-2",
                    "provider": "lmstudio",
                    "model": "gemma",
                    "success": True,
                    "response": "Done",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        class _Resp:
            def __init__(self, status_code: int, payload: dict | None = None):
                self.status_code = status_code
                self._payload = payload or {}

        def _request(method, path, **kwargs):
            if path == "/tasks/task-2":
                return _Resp(200, {"id": "task-2", "title": "Doc", "status": "todo"})
            return _Resp(404)

        def _api_data(resp):
            return getattr(resp, "_payload", {})

        args = argparse.Namespace(task_id="task-2", json=True)
        captured = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), \
            patch("agent.cli_goals._api_data", side_effect=_api_data), \
            patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc), \
            patch("agent.utils.get_data_dir", return_value=str(tmp_path)):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_task_report(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["task"]["task_id"] == "task-2"
        assert parsed["traces"][0]["provider"] == ""
        assert parsed["traces"][0]["response_preview"] == "Done"

    def test_task_inspect_alias_matches_task_report(self, tmp_path):
        import argparse
        import contextlib
        import io
        from agent.cli.prompt_inspect import cmd_prompt_task_report, run_prompt_command

        class _Resp:
            def __init__(self, status_code: int, payload: dict | None = None):
                self.status_code = status_code
                self._payload = payload or {}

        task_detail = {"id": "task-3", "title": "Alias", "status": "todo"}

        def _request(method, path, **kwargs):
            if path == "/tasks/task-3":
                return _Resp(200, task_detail)
            return _Resp(404)

        def _api_data(resp):
            return getattr(resp, "_payload", {})

        args = argparse.Namespace(task_id="task-3", json=True)
        captured_inspect = io.StringIO()
        captured_report = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), \
            patch("agent.cli_goals._api_data", side_effect=_api_data), \
            patch("agent.utils.get_data_dir", return_value=str(tmp_path)):
            with contextlib.redirect_stdout(captured_report):
                report_code = cmd_prompt_task_report(args)
            args.prompt_cmd = "task-inspect"
            with contextlib.redirect_stdout(captured_inspect):
                inspect_code = run_prompt_command(args)

        assert report_code == 0
        assert inspect_code == 0
        assert json.loads(captured_report.getvalue()) == json.loads(captured_inspect.getvalue())


class TestPromptLearningReport:
    def test_learning_report_json_happy_path(self):
        import argparse
        import contextlib
        import io
        from agent.cli.prompt_inspect import cmd_prompt_learning_report

        class _Resp:
            def __init__(self, status_code: int, payload: dict | None = None):
                self.status_code = status_code
                self._payload = payload or {}

        dashboard_payload = {
            "llm_configuration": {
                "planning_learning": {
                    "enabled": True,
                    "policy": {"lookback_runs": 24, "freeze_minutes": 60},
                    "candidate_count": 2,
                    "review_item_count": 1,
                    "profiles": [
                        {
                            "profile_name": "lmstudio_laptop",
                            "enabled": True,
                            "provider": "lmstudio",
                            "model_family": "gemma",
                            "model_name_pattern": "gemma-4e4b",
                            "active_prompt_version_id": "v2",
                            "current_quality_score": 0.25,
                            "trend_direction": "degrading",
                            "current_candidate": {"status": "proposed"},
                            "freeze": {"active": True},
                            "metrics": {"run_count": 12},
                        }
                    ],
                }
            }
        }

        def _request(method, path, **kwargs):
            if path == "/dashboard/read-model":
                return _Resp(200, dashboard_payload)
            return _Resp(404)

        def _api_data(resp):
            return getattr(resp, "_payload", {})

        args = argparse.Namespace(json=True)
        captured = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), patch("agent.cli_goals._api_data", side_effect=_api_data):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_learning_report(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["enabled"] is True
        assert parsed["candidate_count"] == 2
        assert parsed["profiles"][0]["profile_name"] == "lmstudio_laptop"

    def test_learning_report_missing_optional_fields(self):
        import argparse
        import contextlib
        import io
        from agent.cli.prompt_inspect import cmd_prompt_learning_report

        class _Resp:
            def __init__(self, status_code: int, payload: dict | None = None):
                self.status_code = status_code
                self._payload = payload or {}

        dashboard_payload = {
            "llm_configuration": {
                "planning_learning": {
                    "enabled": False,
                    "policy": {},
                    "candidate_count": 0,
                    "review_item_count": 0,
                    "profiles": [{}],
                }
            }
        }

        def _request(method, path, **kwargs):
            if path == "/dashboard/read-model":
                return _Resp(200, dashboard_payload)
            return _Resp(404)

        def _api_data(resp):
            return getattr(resp, "_payload", {})

        args = argparse.Namespace(json=True)
        captured = io.StringIO()
        with patch("agent.cli_goals._request", side_effect=_request), patch("agent.cli_goals._api_data", side_effect=_api_data):
            with contextlib.redirect_stdout(captured):
                code = cmd_prompt_learning_report(args)

        assert code == 0
        parsed = json.loads(captured.getvalue())
        assert parsed["enabled"] is False
        assert parsed["profiles"][0].get("profile_name", "") == ""
