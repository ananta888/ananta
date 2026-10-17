"""Explicit Hub operator pre-authorization, separate from worker configuration."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent.services.hub_evidence_gate_service import canonical_evidence_digest
from agent.services.self_development.budget import unlimited
from agent.services.self_development.contracts import source_path
from agent.services.self_development.scope import profile_roots


class DevelopmentPolicy:
    def __init__(self, path: Path):
        self.path = path
        self.value = json.loads(path.read_text())
        self.digest = canonical_evidence_digest(self.value)
        if self.value.get('enabled') is not True or self.value.get('automatic_publication') is not True:
            raise ValueError('self_development_disabled')
        if self.value['remote_url'] != 'https://github.com/ananta888/ananta.git':
            raise ValueError('development_remote_denied')
        branch = self.value['branch']
        if not branch.startswith('ananta/') or '..' in branch or not branch.replace('/', '').replace('-', '').isalnum():
            raise ValueError('development_branch_denied')
        self.root = Path(self.value['control_root']).resolve()
        self.checkout = self.root / 'checkout'
        self.workspaces = Path(self.value['workspace_root']).resolve()
        if self.root.is_relative_to(self.workspaces):
            raise ValueError('private_control_root_required')
        max_tasks = self.value.get('max_tasks', 1)
        if not unlimited(max_tasks) and not (isinstance(max_tasks, int) and 1 <= max_tasks <= 50):
            raise ValueError('development_budget_invalid')
        max_seconds = self.value.get('max_seconds', 1800)
        if not unlimited(max_seconds) and not (isinstance(max_seconds, int) and 1 <= max_seconds <= 604800):
            raise ValueError('development_seconds_budget_invalid')
        max_attempts = self.value.get('max_attempts', 2)
        if not unlimited(max_attempts) and not (isinstance(max_attempts, int) and 1 <= max_attempts <= 5):
            raise ValueError('development_attempt_budget_invalid')
        if 'skip_unpublished_dependencies' in self.value \
                and not isinstance(self.value['skip_unpublished_dependencies'], bool):
            raise ValueError('development_dependency_policy_invalid')
        self._validate_profiles()
        self._validate_planning()

    def _validate_profiles(self):
        documented_roots = set(self.value.get('allowed_roots') or [])
        for name, profile in (self.value.get('test_profiles') or {}).items():
            if not isinstance(profile, dict) or not isinstance(profile.get('command'), str):
                raise ValueError('development_profile_invalid')
            roots = profile.get('allowed_roots')
            if roots is not None and (not isinstance(roots, list) or not roots
                                      or not set(roots) <= documented_roots):
                raise ValueError('development_profile_invalid')
            globs = profile.get('allowed_new_file_globs')
            if globs is not None and (not isinstance(globs, list) or not globs
                                      or not all(isinstance(g, str) and g.strip()
                                                 and not g.startswith('/') and '..' not in g.split('/')
                                                 for g in globs)):
                raise ValueError('development_profile_invalid')

    def _validate_planning(self):
        if 'planning' not in self.value:
            return
        planning = self.value['planning']
        profiles = set(self.value.get('test_profiles', {}))
        if not isinstance(planning, dict) or not isinstance(planning.get('enabled'), bool):
            raise ValueError('development_planning_invalid')
        max_files = planning.get('max_files', 6)
        if not isinstance(max_files, int) or not 1 <= max_files <= 12:
            raise ValueError('development_planning_invalid')
        allowed = planning.get('allowed_test_profiles') or list(profiles)
        if not isinstance(allowed, list) or not allowed or not set(allowed) <= profiles:
            raise ValueError('development_planning_invalid')
        default = planning.get('default_test_profile')
        if default is not None and default not in allowed:
            raise ValueError('development_planning_invalid')
        scopes = planning.get('commit_scopes') or ['autopilot']
        if not isinstance(scopes, list) or not scopes or not all(
                isinstance(scope, str) and re.fullmatch(r'[a-z0-9-]{1,32}', scope) for scope in scopes):
            raise ValueError('development_planning_invalid')
        for key in ('provider', 'model', 'base_url'):
            if key in planning and planning[key] is not None and not isinstance(planning[key], str):
                raise ValueError('development_planning_invalid')
        base_url = planning.get('base_url')
        if base_url and not re.fullmatch(r'https?://[^\s]+', base_url):
            raise ValueError('development_planning_invalid')
        plan_attempts = planning.get('max_attempts', 2)
        if not isinstance(plan_attempts, int) or not 1 <= plan_attempts <= 5:
            raise ValueError('development_planning_invalid')

    def planning_enabled(self):
        return bool((self.value.get('planning') or {}).get('enabled'))

    def assert_current(self):
        if canonical_evidence_digest(json.loads(self.path.read_text())) != self.digest:
            raise ValueError('development_policy_changed')

    def authorize_contract(self, contract):
        profile = self.value['test_profiles'].get(contract['test_profile'])
        if not profile or not isinstance(profile.get('command'), str):
            raise ValueError('test_profile_not_authorized')
        allowed_roots = profile_roots(self.value, contract['test_profile'])
        for path in contract['files']:
            source_path(path)
            if not any(path.startswith(root.rstrip('/') + '/') for root in allowed_roots):
                raise ValueError('task_scope_not_authorized')
        self._assert_profile_covers_tests(profile, contract['files'])
        return profile

    @staticmethod
    def _assert_profile_covers_tests(profile, files):
        """A declared test file must run in its profile's verification.

        ``completion_rule: profile_passed`` only speaks for the change when the verification can
        see it: live, a published ``tests/e2e`` file never executed under its profile and arrived
        with a syntax error. Non-test files (docs, scripts, sources) are verified behaviourally
        and are covered by the source gate instead.
        """
        tokens = str(profile.get('command') or '').split()
        root = tokens[-1] if tokens else ''
        if not root.startswith('tests'):
            return
        prefix = root.rstrip('/')
        for path in files:
            if path.startswith('tests/') and path != prefix and not path.startswith(prefix + '/'):
                raise ValueError('test_profile_does_not_cover_files:' + path)
