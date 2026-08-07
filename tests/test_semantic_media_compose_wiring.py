from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_ROOT = ROOT / "docker" / "compose-next"

HUB_MEDIA_ENVIRONMENT = {
    "ANANTA_ORDINARY_MEDIA_PUBLICATION_ENABLED": ("${ANANTA_ORDINARY_MEDIA_PUBLICATION_ENABLED:-false}"),
    "ANANTA_SEMANTIC_VISUAL_CAPTURE_ENABLED": ("${ANANTA_SEMANTIC_VISUAL_CAPTURE_ENABLED:-false}"),
    "ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED": ("${ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED:-false}"),
    "ANANTA_SEMANTIC_MEDIA_SFU_ENABLED": ("${ANANTA_SEMANTIC_MEDIA_SFU_ENABLED:-false}"),
}


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_compose_tag(loader: _ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_compose_tag)
_ComposeLoader.add_constructor("!reset", _construct_compose_tag)


def _load(name: str) -> dict:
    document = yaml.load(
        (COMPOSE_ROOT / name).read_text(encoding="utf-8"),
        Loader=_ComposeLoader,
    )
    assert isinstance(document, dict)
    return document


def _assert_media_policy(environment: dict[str, object]) -> None:
    assert {key: environment.get(key) for key in HUB_MEDIA_ENVIRONMENT} == HUB_MEDIA_ENVIRONMENT


def test_hub_media_policy_is_wired_into_every_environment_boundary() -> None:
    base = _load("compose.base.yml")
    test_stack = _load("compose.tests.lmstudio.yml")
    production = _load("compose.workflow-runtime.production.yml")

    _assert_media_policy(base["services"]["ai-agent-hub-base"]["environment"])
    _assert_media_policy(test_stack["services"]["ai-agent-hub"]["environment"])
    _assert_media_policy(production["services"]["ai-agent-hub"]["environment"])


def test_hub_media_policy_is_not_delegated_to_workers() -> None:
    base = _load("compose.base.yml")
    test_stack = _load("compose.tests.lmstudio.yml")
    production = _load("compose.workflow-runtime.production.yml")

    worker_environments = [
        base["services"]["ai-agent-worker-base"]["environment"],
        test_stack["services"]["ai-agent-alpha"]["environment"],
        test_stack["services"]["ai-agent-beta"]["environment"],
        production["services"]["ai-agent-alpha"]["environment"],
        production["services"]["ai-agent-beta"]["environment"],
    ]
    for environment in worker_environments:
        assert set(HUB_MEDIA_ENVIRONMENT).isdisjoint(environment)
