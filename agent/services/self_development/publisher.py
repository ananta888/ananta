"""Exact-change-set Git publication after a Hub evidence gate succeeds."""
from __future__ import annotations

from agent.services.git_ops_service import GitOpsService
from agent.services.ops_policy_service import OpsPolicyDecision, OpsPolicyService
from agent.services.ops_registry_service import OpsRegistryService, WorkspaceRef
from agent.services.self_development.transport import REFERENCE, transport
from agent.services.self_development.workspace import fingerprint, git, read_source


class VerifiedPublicationPolicy:
    """A single closed, Hub pre-authorized publication, never a global Ops grant."""
    def __init__(self, policy, files, message, verify):
        self.policy, self.files, self.message, self.verify = policy, files, message, verify

    def authorize(self, tool_name, action, *, target_id, arguments, approval_id):
        self.policy.assert_current()
        self.verify()
        from flask import current_app
        cfg = current_app.config.get('AGENT_CONFIG', {})
        required = cfg.get('approval_lifecycle', {}).get('human_required_tools', [])
        base = OpsPolicyService().evaluate(tool_name, action, target_id=target_id)
        expected = {
            'git.stage': {'workspace_id': 'self-development', 'paths': self.files},
            'git.commit': {'workspace_id': 'self-development', 'message': self.message},
            'git.push': {'workspace_id': 'self-development', 'remote': 'origin',
                         'branch': self.policy.value['branch'], 'credential_ref': REFERENCE},
        }
        valid = (tool_name not in required and base.decision != 'policy_denied'
                 and target_id == 'self-development' and arguments == expected.get(tool_name))
        return OpsPolicyDecision('allow' if valid else 'policy_denied',
            'verified_development_pre_authorization' if valid else 'publication_binding_denied')

    def create_approval_request(self, **kwargs):
        return None

    def consume_approval(self, approval_id):
        return None


class Publisher:
    def __init__(self, policy, *, service_factory=GitOpsService):
        self.policy = policy
        self.service_factory = service_factory

    def publish(self, item, verify, persist):
        policy = self.policy
        policy.assert_current()
        verify()
        hashes = item['hashes']
        seal = policy.root / 'sealed' / item['task_id']
        if fingerprint(seal, sorted(hashes)) != hashes:
            raise ValueError('sealed_snapshot_changed')
        if git(policy.checkout, 'remote', 'get-url', 'origin') != policy.value['remote_url']:
            raise ValueError('publication_remote_changed')
        if git(policy.checkout, 'branch', '--show-current') != policy.value['branch']:
            raise ValueError('publication_branch_changed')
        runner, remote_policy = transport(policy)
        grant = VerifiedPublicationPolicy(policy, sorted(hashes), item['contract']['commit_message'], verify)
        service = self.service_factory(registry=OpsRegistryService(workspaces=[
            WorkspaceRef('self-development', policy.checkout)]), runner=runner, policy=grant,
            remote_policy=remote_policy)
        if not item.get('commit_sha'):
            if git(policy.checkout, 'rev-parse', 'HEAD') != item['revision']:
                raise ValueError('publication_revision_changed')
            if git(policy.checkout, 'status', '--porcelain'):
                raise ValueError('publication_checkout_dirty')
            for relative in sorted(hashes):
                path = policy.checkout / relative
                # The private checkout is never writable by a Worker.
                if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                    raise ValueError('publication_symlink_denied')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(read_source(seal, relative))
            for result in (service.stage('self-development', sorted(hashes)),):
                self._require(result)
            staged = git(policy.checkout, 'diff', '--cached', '--name-only').splitlines()
            if sorted(staged) != sorted(hashes) or fingerprint(policy.checkout, staged) != hashes:
                raise ValueError('publication_index_mismatch')
            self._require(service.commit('self-development', item['contract']['commit_message']))
            item['commit_sha'] = git(policy.checkout, 'rev-parse', 'HEAD')
            persist()  # Retry a failed push without creating another commit.
        elif git(policy.checkout, 'rev-parse', 'HEAD') != item['commit_sha']:
            raise ValueError('publication_commit_changed')
        self._require(service.push('self-development', remote='origin',
            branch=policy.value['branch'], credential_ref=REFERENCE))
        item['status'] = 'published'
        persist()
        return item['commit_sha']

    @staticmethod
    def _require(result):
        if not result.ok:
            raise ValueError('publication_' + (result.error.code if result.error else 'failed'))
