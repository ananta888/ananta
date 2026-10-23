"""Single-source scope: one policy profile drives contract roots, worker
mutation globs and allowlisted test commands.

Keeping these three in one place removes the drift where the planner proposed
files the Worker's mutation policy refused. Nothing here widens a Worker scope
on its own; it only *derives* the value the operator already declared.
"""
from __future__ import annotations


def profile(policy_value: dict, name: str) -> dict:
    profiles = policy_value.get('test_profiles') or {}
    return dict(profiles.get(name) or {})


def profile_roots(policy_value: dict, name: str) -> list[str]:
    profile_cfg = profile(policy_value, name)
    roots = profile_cfg.get('allowed_roots') or policy_value.get('allowed_roots') or []
    return [str(root).rstrip('/') for root in roots]


def planning_profile_names(policy_value: dict) -> list[str]:
    planning = policy_value.get('planning') or {}
    allowed = planning.get('allowed_test_profiles')
    if allowed:
        return [str(name) for name in allowed]
    return list(policy_value.get('test_profiles') or {})


def allowed_roots_for_contracts(policy_value: dict, names=None) -> list[str]:
    roots: set[str] = set()
    for name in (names if names is not None else planning_profile_names(policy_value)):
        roots.update(profile_roots(policy_value, name))
    return sorted(roots)


def _profile_globs(policy_value: dict, name: str) -> list[str]:
    declared = profile(policy_value, name).get('allowed_new_file_globs')
    if declared:
        return [str(glob) for glob in declared]
    # fnmatch treats '*' as matching '/', so '<root>/*' already covers the
    # whole subtree, including files directly under the root.
    return [f'{root}/*' for root in profile_roots(policy_value, name)]


def required_worker_mutation(policy_value: dict) -> dict:
    """The Worker mutation allowlist this policy needs to execute its tasks."""
    globs: set[str] = set()
    commands: list[str] = []
    for name in (policy_value.get('test_profiles') or {}):
        globs.update(_profile_globs(policy_value, name))
        command = profile(policy_value, name).get('command')
        if isinstance(command, str) and command.strip():
            commands.append(command)
    return {'allowed_new_file_globs': sorted(globs), 'allowlisted_test_commands': commands}


def merge_worker_mutation(policy_value: dict, mutation_cfg: dict | None) -> tuple[dict, bool]:
    """Union the derived scope into an ``ananta_worker_workspace_mutation`` block."""
    current = dict(mutation_cfg or {})
    required = required_worker_mutation(policy_value)
    globs = list(current.get('allowed_new_file_globs') or [])
    commands = list(current.get('allowlisted_test_commands') or [])
    for glob in required['allowed_new_file_globs']:
        if glob not in globs:
            globs.append(glob)
    for command in required['allowlisted_test_commands']:
        if command not in commands:
            commands.append(command)
    changed = (globs != list(current.get('allowed_new_file_globs') or [])
               or commands != list(current.get('allowlisted_test_commands') or []))
    current['allowed_new_file_globs'] = globs
    current['allowlisted_test_commands'] = commands
    return current, changed
