"""Optional real no-tools SDK turn; technical observation, never a grounded release run."""

import json
import os
from threading import Event

import pytest

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
from agent.cli_backends.pi_configuration import PI_PROVIDER_NAME, isolated_pi_configuration
from agent.cli_backends.pi_events import parse_pi_one_shot
from agent.cli_backends.pi_runtime import pi_sdk_command
from tests.pi.pi_live_smoke_configuration import live_smoke_selection
from worker.runtime.native_graph.pi_process import pi_execution_launch


@pytest.mark.timeout(45)
@pytest.mark.parametrize('kind', ['local', 'external'])
def test_explicit_real_pi_no_tools_smoke(tmp_path, monkeypatch, kind, record_property):
    selection = live_smoke_selection(kind, os.environ)
    if selection is None:
        pytest.skip('explicit Pi live model smoke opt-in required')
    binary = selection.sdk.parent / 'bundle' / 'cli.js'
    if not binary.is_file():
        pytest.fail('pi_live_smoke_sdk_entry_missing', pytrace=False)
    command = pi_sdk_command(str(binary), path_environment=str(selection.node.parent))
    project = tmp_path / 'project'
    project.mkdir(mode=0o700)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    result_store = private / 'results.sqlite3'
    result_store.write_text('synthetic-private-live-smoke-canary')
    monkeypatch.setenv('ANANTA_NATIVE_RESULT_STORE_PATH', str(result_store))
    # Context selection is explicit; this smoke must not query a serving profile.
    monkeypatch.setattr('agent.cli_backends.pi_configuration.pi_context_window', lambda _target: 8192)
    with isolated_pi_configuration(
        selection.target, project=project, runtime_root=tmp_path, max_tokens=32,
    ) as invocation:
        result = BoundedCodingAgentProcess(launch_context=pi_execution_launch).run(
            (*command, str(invocation.config_directory)), cwd=invocation.cwd,
            environment={'PATH': str(selection.node.parent), 'PI_CODING_AGENT_DIR': str(invocation.config_directory),
                         'ANANTA_PI_API_KEY': selection.target.api_key},
            input_text='Return one short greeting. No tools, files or actions.\n', timeout_seconds=30,
            cancellation=Event(), maximum_output_chars=65536, secret_values=(selection.target.api_key,),
        )
        # Pytest assertion rewriting can include the complete result repr,
        # including private provider output. Export only a fixed reason.
        if result.return_code != 0:
            raise ValueError('pi_live_smoke_process_failed')
        answer = parse_pi_one_shot(result.stdout, provider=PI_PROVIDER_NAME,
                                   model=selection.target.model, workspace=invocation.cwd)
        assert answer.strip(), 'pi_live_smoke_response_missing'
        assert not (invocation.config_directory / 'models-cache.json').exists()
    assert result_store.read_text() == 'synthetic-private-live-smoke-canary'
    record_property('pi_live_technical_observation', json.dumps({'kind': kind, 'reply_characters': len(answer),
        'tools': False, 'production_release_evidence': False, 'hub_dispatch_verified': False}))
