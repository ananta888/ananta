"""Hub-owned contract planning inside an operator-authored envelope.

The planner only ever *proposes* an execution contract. It cannot widen write
scope or pick an unregistered test profile: the Hub validates every proposal
against ``contracts.task_contract`` and ``DevelopmentPolicy.authorize_contract``
before it is admitted. This keeps the "model output cannot expand authorization"
boundary intact (DIP: the coordinator depends on the ``ContractPlanner`` port,
not on a concrete model transport).
"""
from __future__ import annotations

import json
import re
from typing import Protocol

_JSON_FENCE = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.DOTALL)
_COMMIT_SCOPE = re.compile(r'^[a-z]+\(([a-z0-9-]+)\): ')


class PlanningEnvelope:
    """Bounded operator policy a planner proposal must stay inside."""

    def __init__(self, *, allowed_roots, allowed_test_profiles, default_test_profile,
                 commit_scopes, max_files, repository, profile_roots=None):
        self.allowed_roots = tuple(allowed_roots)
        self.allowed_test_profiles = tuple(allowed_test_profiles)
        self.default_test_profile = default_test_profile
        self.commit_scopes = tuple(commit_scopes)
        self.max_files = int(max_files)
        self.repository = tuple(repository)
        self.profile_roots = dict(profile_roots or {})

    @classmethod
    def from_policy(cls, policy, repository):
        from agent.services.self_development.scope import (
            allowed_roots_for_contracts,
            planning_profile_names,
            profile_roots,
        )
        value = dict(policy.value.get('planning') or {})
        allowed = tuple(planning_profile_names(policy.value))
        default = str(value.get('default_test_profile') or (allowed[0] if allowed else ''))
        roots = allowed_roots_for_contracts(policy.value, allowed) or list(policy.value.get('allowed_roots', ()))
        return cls(allowed_roots=tuple(roots),
                   allowed_test_profiles=allowed, default_test_profile=default,
                   commit_scopes=tuple(value.get('commit_scopes') or ('autopilot',)),
                   max_files=int(value.get('max_files', 6)), repository=tuple(repository),
                   profile_roots={name: tuple(profile_roots(policy.value, name)) for name in allowed})

    def scope_of(self, commit_message: str) -> str | None:
        match = _COMMIT_SCOPE.match(commit_message or '')
        return match.group(1) if match else None


class ContractPlanner(Protocol):
    def propose(self, *, item: dict, envelope: PlanningEnvelope, error: str | None = None) -> dict: ...


def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from a model response, fenced or bare."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('planner_output_empty')
    fenced = _JSON_FENCE.search(text)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end <= start:
            raise ValueError('planner_output_not_json')
        candidate = text[start:end + 1]
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError('planner_output_invalid_json') from exc
    if not isinstance(parsed, dict):
        raise ValueError('planner_output_not_object')
    return parsed


def render_planning_prompt(item: dict, envelope: PlanningEnvelope, error: str | None = None) -> str:
    repository = '\n'.join(envelope.repository)
    profiles = '\n'.join(
        f'  - {name}: change files under {", ".join(envelope.profile_roots.get(name, envelope.allowed_roots))}'
        for name in envelope.allowed_test_profiles) or '  (none)'
    correction = ''
    if error:
        correction = ('\nYour previous proposal was rejected: ' + error
                      + '\nReturn a corrected JSON object that satisfies every constraint.\n')
    return (
        'You plan one bounded source change for an autonomous coding run.\n'
        'Return ONLY a JSON object, no prose, with exactly these keys:\n'
        '  "files": list of existing or new repository paths to create/modify,\n'
        '  "test_profile": one allowed profile name,\n'
        '  "commit_message": conventional commit "type(scope): subject".\n'
        'Hard constraints:\n'
        f'- every file path must start with one of: {", ".join(envelope.allowed_roots)}\n'
        f'- at most {envelope.max_files} files\n'
        f'- test_profile must be one of: {", ".join(envelope.allowed_test_profiles)}\n'
        f'- commit scope must be one of: {", ".join(envelope.commit_scopes)}\n'
        '- reference only files that exist in the repository listing below when reading,\n'
        '  but you may add new files inside the allowed roots.\n'
        'Choose the test profile whose area best matches the changed files:\n'
        f'{profiles}\n'
        f'{correction}'
        f'\nTODO id: {item.get("todo_id")}\n'
        f'TODO title: {item.get("title")}\n'
        f'TODO description: {item.get("description")}\n'
        f'\nRepository files (allowed roots):\n{repository}\n'
    )


class HubModelContractPlanner:
    """Concrete adapter that calls a Hub-owned text generator (injected)."""

    def __init__(self, generate_text):
        self._generate_text = generate_text

    def propose(self, *, item: dict, envelope: PlanningEnvelope, error: str | None = None) -> dict:
        prompt = render_planning_prompt(item, envelope, error=error)
        return extract_json_object(self._generate_text(prompt))
