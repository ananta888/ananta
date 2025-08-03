import yaml
from pathlib import Path

def test_services_present():
    compose = yaml.safe_load(Path('docker-compose.yml').read_text())
    services = compose.get('services', {})
    assert 'controller' in services
    assert 'ai-agent' in services


def test_cpu_gpu_allocation():
    compose = yaml.safe_load(Path('docker-compose.yml').read_text())
    services = compose['services']

    ctrl_env = services['controller'].get('environment', {})
    if isinstance(ctrl_env, list):
        ctrl_env = dict(item.split('=', 1) for item in ctrl_env)
    assert ctrl_env.get('OLLAMA_DEVICE') == 'cpu'

    agent_env = services['ai-agent'].get('environment', {})
    if isinstance(agent_env, list):
        agent_env = dict(item.split('=', 1) for item in agent_env)
    assert agent_env.get('OLLAMA_DEVICE') == 'gpu'

    devices = (
        services['ai-agent']
        .get('deploy', {})
        .get('resources', {})
        .get('reservations', {})
        .get('devices', [])
    )
    assert any('gpu' in d.get('capabilities', []) for d in devices)
