"""Owned loopback model with real pinned SDK and existing Hub authority/budget ports."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
from agent.cli_backends.pi_runtime import pi_sdk_command
from tests.pi.test_pi_native_node import native_setup
from worker.runtime.native_graph.pi_process import pi_execution_launch


@pytest.mark.timeout(45)
def test_native_worker_executes_real_sdk_only_at_hub_selected_endpoint(tmp_path, monkeypatch):
    node, sdk = os.environ.get('ANANTA_TEST_NODE_BINARY'), os.environ.get('ANANTA_TEST_PI_SDK')
    if not node or not sdk:
        pytest.skip('Explicit Node and pinned SDK resources required')
    node, sdk = Path(node).resolve(strict=True), Path(sdk).resolve(strict=True)
    binary = sdk.parent / 'bundle' / 'cli.js'
    argv = pi_sdk_command(str(binary), path_environment=str(node.parent))
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    canary = private / 'results.sqlite3'
    canary.write_text('synthetic-private-result')
    monkeypatch.setenv('ANANTA_NATIVE_RESULT_STORE_PATH', str(canary))
    monkeypatch.setattr('agent.cli_backends.pi_configuration.pi_context_window', lambda target: 8192)
    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 200000:
                self.send_error(400)
                return
            requests.append((self.path, json.loads(self.rfile.read(length))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, reason in [({'role': 'assistant', 'content': 'bounded native SDK answer'}, None), ({}, 'stop')]:
                chunk = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
        runtime = {'installed': True, 'status': 'ready', 'version': '0.85.1', 'binary_path': str(binary),
            'version_probe': {'rc': 0, 'stdout': '0.85.1', 'stderr': ''}}
        adapter, task, _, _, _ = native_setup(tmp_path, endpoint_identity=endpoint,
            runner=BoundedCodingAgentProcess(launch_context=pi_execution_launch),
            provider_options={'runtime_probe': lambda: runtime, 'command_builder': lambda _: argv,
                              'environment': {'PATH': str(node.parent)}})
        result = adapter.execute_task(task)
        if result.status != 'completed':
            raise ValueError('pi_native_pinned_sdk_execution_failed:' + result.reason_code)
        assert result.output_data['output'] == 'bounded native SDK answer'
        assert len(requests) == 1
        assert requests[0][0] == '/v1/chat/completions'
        assert requests[0][1]['model'] == 'selected-model'
        assert not requests[0][1].get('tools')
        assert canary.read_text() == 'synthetic-private-result'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
