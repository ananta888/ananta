"""Opt-in real pinned SDK gate; only the loopback model response is synthetic."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
from agent.cli_backends.pi_configuration import isolated_pi_configuration
from agent.cli_backends.pi_runtime import pi_sdk_command
from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget
from worker.runtime.native_graph.pi_process import pi_execution_launch


def test_pinned_sdk_runs_one_no_tools_turn_inside_private_store_boundary(tmp_path, monkeypatch):
    node = os.environ.get("ANANTA_TEST_NODE_BINARY")
    sdk = os.environ.get("ANANTA_TEST_PI_SDK")
    if not node or not sdk:
        pytest.skip("Explicit Node and pinned SDK test resources required")
    node = Path(node).resolve(strict=True)
    sdk = Path(sdk).resolve(strict=True)
    binary = sdk.parent / "bundle" / "cli.js"
    command = pi_sdk_command(str(binary), path_environment=str(node.parent))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    secret = private / "results.sqlite3"
    secret.write_text("synthetic-private-result")
    monkeypatch.setenv("ANANTA_NATIVE_RESULT_STORE_PATH", str(secret))
    monkeypatch.setattr("agent.cli_backends.pi_configuration.pi_context_window", lambda _target: 8192)
    requests = []

    class ModelEndpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size < 200_000:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(size))
            requests.append((self.path, body, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "bounded SDK answer"},
                              "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            for chunk in chunks:
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = tmp_path / "project"
    project.mkdir()
    target = CodingAgentInferenceTarget(
        client_id="pi", provider_id="ollama", model="synthetic-selected", cli_model="synthetic-selected",
        base_url=f"http://127.0.0.1:{server.server_port}/v1", target_kind="local_openai",
        api_key="synthetic-selected-key",
    )
    try:
        with isolated_pi_configuration(target, project=project, runtime_root=tmp_path, max_tokens=64) as invocation:
            result = BoundedCodingAgentProcess(launch_context=pi_execution_launch).run(
                (*command, str(invocation.config_directory)), cwd=invocation.cwd,
                environment={"PATH": str(node.parent), "PI_CODING_AGENT_DIR": str(invocation.config_directory),
                             "ANANTA_PI_API_KEY": target.api_key, "AGENT_TOKEN": "synthetic-hub-token"},
                input_text="Complete the delegated synthetic turn.\n", timeout_seconds=15,
                cancellation=Event(), maximum_output_chars=32_768, secret_values=(target.api_key,),
            )
            assert result.return_code == 0, result.stderr
            assert "bounded SDK answer" in result.stdout
            assert not (invocation.config_directory / "models-cache.json").exists()
        assert len(requests) == 1
        path, body, authorization = requests[0]
        assert path == "/v1/chat/completions"
        assert body["model"] == "synthetic-selected" and body["max_tokens"] == 64
        assert body.get("tools", []) == [] and body["stream"] is True
        assert authorization == "Bearer synthetic-selected-key"
        assert secret.read_text() == "synthetic-private-result"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
