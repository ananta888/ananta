"""Explicit Hub operator pre-authorization, separate from worker configuration."""
from __future__ import annotations

import json
from pathlib import Path

from agent.services.self_development.contracts import source_path
from agent.services.hub_evidence_gate_service import canonical_evidence_digest


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
        if not 1 <= self.value.get('max_tasks', 1) <= 50:
            raise ValueError('development_budget_invalid')

    def assert_current(self):
        if canonical_evidence_digest(json.loads(self.path.read_text())) != self.digest:
            raise ValueError('development_policy_changed')

    def authorize_contract(self, contract):
        profile = self.value['test_profiles'].get(contract['test_profile'])
        if not profile or not isinstance(profile.get('command'), str):
            raise ValueError('test_profile_not_authorized')
        allowed_roots = self.value['allowed_roots']
        for path in contract['files']:
            source_path(path)
            if not any(path.startswith(root.rstrip('/') + '/') for root in allowed_roots):
                raise ValueError('task_scope_not_authorized')
        return profile
