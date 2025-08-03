import yaml
from pathlib import Path

def test_services_present():
    compose = yaml.safe_load(Path('docker-compose.yml').read_text())
    services = compose.get('services', {})
    assert 'controller' in services
    assert 'ai-agent' in services
