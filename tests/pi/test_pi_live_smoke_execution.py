"""Synthetic process ports prove the live harness fences bad outcomes and cleans owned configuration."""

import json
from types import SimpleNamespace

import pytest

from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget
from tests.pi import test_pi_live_smoke as smoke
from tests.pi.pi_live_smoke_configuration import PiLiveSmokeSelection
from tests.pi.pi_protocol_examples import pi_events


def fixture(tmp_path, monkeypatch, *, outcome='success'):
    sdk = tmp_path / 'sdk' / 'dist' / 'index.js'
    sdk.parent.mkdir(parents=True)
    sdk.write_text('synthetic SDK')
    bundle = sdk.parent / 'bundle'
    bundle.mkdir()
    (bundle / 'cli.js').write_text('synthetic CLI')
    node = tmp_path / 'node'
    node.write_text('synthetic Node')
    target = CodingAgentInferenceTarget(client_id='pi', provider_id='ollama', model='selected-model',
        cli_model='selected-model', base_url='http://127.0.0.1:18080/v1', target_kind='local_openai',
        api_key='synthetic-selected-private-token')
    selection = PiLiveSmokeSelection(node, sdk, target)
    monkeypatch.setattr(smoke, 'live_smoke_selection', lambda kind, environment: selection)
    monkeypatch.setattr(smoke, 'pi_sdk_command', lambda *args, **kwargs: ('node', 'entry', str(sdk)))
    calls, config_paths = [], []

    class Process:
        def __init__(self, *, launch_context):
            assert launch_context is smoke.pi_execution_launch

        def run(self, argv, **options):
            calls.append(options)
            config_paths.append(options['cwd'].parent)
            assert options['timeout_seconds'] == 30 and options['maximum_output_chars'] == 65536
            assert options['environment']['ANANTA_PI_API_KEY'] == target.api_key
            assert 'AGENT_TOKEN' not in options['environment']
            if outcome == 'failure':
                return SimpleNamespace(return_code=1, stdout='', stderr=target.api_key)
            if outcome == 'canary':
                (tmp_path / 'private' / 'results.sqlite3').write_text('mutated')
            events = pi_events(workspace=str(options['cwd']), model=target.model, answer='synthetic answer')
            if outcome == 'model':
                events[7]['message']['model'] = 'unselected-model'
            if outcome == 'tools':
                events.insert(4, {'type': 'tool_execution_start'})
            return SimpleNamespace(return_code=0, stdout='\n'.join(map(json.dumps, events)), stderr='')

    monkeypatch.setattr(smoke, 'BoundedCodingAgentProcess', Process)
    return calls, config_paths


def test_smoke_success_is_only_technical_and_preserves_isolation_selection(tmp_path, monkeypatch):
    calls, paths = fixture(tmp_path, monkeypatch)
    recorded = {}
    smoke.test_explicit_real_pi_no_tools_smoke(tmp_path, monkeypatch, 'local',
        lambda key, value: recorded.update({key: json.loads(value)}))
    result = recorded['pi_live_technical_observation']
    assert result['production_release_evidence'] is False and result['hub_dispatch_verified'] is False
    assert result['tools'] is False and len(calls) == 1
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize('outcome', ['failure', 'model', 'tools', 'canary'])
def test_bad_process_binding_tools_or_canary_never_become_live_acceptance(tmp_path, monkeypatch, outcome):
    calls, paths = fixture(tmp_path, monkeypatch, outcome=outcome)
    recorded = []
    with pytest.raises((AssertionError, ValueError)) as error:
        smoke.test_explicit_real_pi_no_tools_smoke(tmp_path, monkeypatch, 'local',
            lambda *arguments: recorded.append(arguments))
    assert 'synthetic-selected-private-token' not in str(error.value)
    assert not recorded and len(calls) == 1 and all(not path.exists() for path in paths)
