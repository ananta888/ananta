"""Explicit test operator selection; no provider discovery or execution authority."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent.cli_backends.pi_configuration import validate_pi_target
from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget


@dataclass(frozen=True)
class PiLiveSmokeSelection:
    node: Path
    sdk: Path
    target: CodingAgentInferenceTarget


def live_smoke_selection(kind: str, environment: Mapping[str, str]) -> PiLiveSmokeSelection | None:
    if kind not in {'local', 'external'}:
        raise ValueError('pi_live_smoke_kind_invalid')
    prefix = 'ANANTA_TEST_PI_LIVE_' + kind.upper()
    flag = environment.get(prefix, '0')
    if flag not in {'0', '1'}:
        raise ValueError('pi_live_smoke_opt_in_invalid')
    if flag == '0':
        return None
    resources = []
    for key in ('ANANTA_TEST_NODE_BINARY', 'ANANTA_TEST_PI_SDK'):
        value = environment.get(key)
        if not value:
            raise ValueError('pi_live_smoke_resource_missing')
        try:
            resource = Path(value).resolve(strict=True)
        except (OSError, ValueError):
            raise ValueError('pi_live_smoke_resource_missing') from None
        if not resource.is_file():
            raise ValueError('pi_live_smoke_resource_invalid')
        resources.append(resource)
    target = CodingAgentInferenceTarget(client_id='pi', provider_id='ollama' if kind == 'local' else 'openrouter',
        model=environment.get(prefix + '_MODEL'), cli_model=environment.get(prefix + '_MODEL'),
        base_url=environment.get(prefix + '_BASE_URL'),
        target_kind='local_openai' if kind == 'local' else 'remote_openai',
        api_key=environment.get(prefix + '_API_KEY'))
    reason = validate_pi_target(target)
    if reason:
        raise ValueError(reason)
    return PiLiveSmokeSelection(resources[0], resources[1], target)
