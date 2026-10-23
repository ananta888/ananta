"""Exact-change-set Git publication after a Hub evidence gate succeeds."""
from __future__ import annotations

import os

from agent.services.git_ops_service import GitOpsService
from agent.services.ops_policy_service import OpsPolicyDecision, OpsPolicyService
from agent.services.ops_registry_service import OpsRegistryService, WorkspaceRef
from agent.services.self_development.transport import REFERENCE, transport
from agent.services.self_development.workspace import fingerprint, git, is_ancestor, read_source


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
            'git.fetch': {'workspace_id': 'self-development', 'remote': 'origin',
                          'credential_ref': REFERENCE},
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
        # A change set is content (hashes) plus deletions (removed): a completed TODO document
        # is added under todos/archiv and its previous path is removed in the same commit.
        removed = [str(relative) for relative in (item.get('removed') or [])]
        change_set = sorted(set(hashes) | set(removed))
        seal = policy.root / 'sealed' / item['task_id']
        if fingerprint(seal, sorted(hashes)) != hashes:
            raise ValueError('sealed_snapshot_changed')
        if git(policy.checkout, 'remote', 'get-url', 'origin') != policy.value['remote_url']:
            raise ValueError('publication_remote_changed')
        if git(policy.checkout, 'branch', '--show-current') != policy.value['branch']:
            raise ValueError('publication_branch_changed')
        runner, remote_policy = transport(policy)
        grant = VerifiedPublicationPolicy(policy, change_set, item['contract']['commit_message'], verify)
        service = self.service_factory(registry=OpsRegistryService(workspaces=[
            WorkspaceRef('self-development', policy.checkout)]), runner=runner, policy=grant,
            remote_policy=remote_policy)
        landed = self._integrate_branch(item, service, change_set)
        if not landed and self._sealed_landed(item):
            # The integrated history already carries these exact bytes -- a replayed
            # chain commit or an identical re-run. Record the carrying commit instead
            # of writing a second one.
            item['commit_sha'] = git(policy.checkout, 'log', '-1', '--format=%H', '--', *change_set)
            landed = True
        if not landed:
            self._land_change(item, service, change_set, seal, removed, persist)
        self._require(service.push('self-development', remote='origin',
            branch=policy.value['branch'], credential_ref=REFERENCE))
        item['status'] = 'published'
        persist()
        return item['commit_sha']

    def _integrate_branch(self, item, service, change_set):
        """Land the closed checkout on the live publication branch.

        The remote branch advances outside this loop (operator merges, sibling
        publications). The live tip is merged into the unpushed publication chain so
        no accepted commit is dropped or rewritten: a merged chain keeps every item's
        recorded ``commit_sha`` valid. Returns ``True`` when the item's commit already
        sits on the remote tip: a push-retry after a crash must complete the
        bookkeeping, never rebuild the change.
        """
        checkout = self.policy.checkout
        head = git(checkout, 'rev-parse', 'HEAD')
        revision = item.get('revision')
        if revision:
            try:
                explained = is_ancestor(checkout, revision, head) or is_ancestor(checkout, head, revision)
            except ValueError:
                explained = False
            if not explained:
                # Only loop-internal movement (merges, sibling publications) may sit
                # between the admitted base and the checkout head. A foreign history
                # is a closed failure, never a silent rebase of unknown state.
                raise ValueError('publication_checkout_moved')
        if git(checkout, 'status', '--porcelain'):
            raise ValueError('publication_checkout_dirty')
        self._require(service.fetch('self-development', remote='origin', credential_ref=REFERENCE))
        tip = git(checkout, 'rev-parse', 'origin/' + self.policy.value['branch'])
        item['published_base'] = tip
        committed = item.get('commit_sha')
        if committed and is_ancestor(checkout, committed, tip):
            return True
        if head != tip:
            # Behind or diverged: one merge absorbs the tip into the chain (fast-forward
            # when possible). A concurrent remote change to files the chain already
            # rewrote fails closed instead of silently picking a winner.
            try:
                git(checkout, 'merge', '--no-edit', tip)
            except ValueError:
                try:
                    git(checkout, 'merge', '--abort')
                except ValueError:
                    pass
                raise ValueError('publication_merge_conflict')
        return False

    def _sealed_landed(self, item):
        """Whether the working tree already holds the sealed content of the change set.

        Only a change that was landed before (``commit_sha`` recorded) may finish by
        content: a fresh item whose bytes coincide with the tree would hide the exact
        change-set verification behind a no-op.
        """
        if not item.get('commit_sha'):
            return False
        checkout = self.policy.checkout
        for relative in item['hashes']:
            path = checkout / relative
            if path.is_symlink() or not path.is_file():
                return False
        if fingerprint(checkout, sorted(item['hashes'])) != item['hashes']:
            return False
        return not any((checkout / str(relative)).exists() for relative in (item.get('removed') or []))

    def _land_change(self, item, service, change_set, seal, removed, persist):
        policy = self.policy
        hashes = item['hashes']
        if git(policy.checkout, 'diff', '--name-only', item['revision'], 'HEAD', '--', *change_set):
            raise ValueError('publication_base_changed_files')
        for relative in sorted(hashes):
            path = policy.checkout / relative
            # The private checkout is never writable by a Worker.
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                raise ValueError('publication_symlink_denied')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(read_source(seal, relative))
            # An executable sealed file stays executable: a gate script that cannot run is
            # exactly what the source gate refuses to publish.
            if os.access(seal / relative, os.X_OK):
                path.chmod(path.stat().st_mode | 0o111)
        for relative in sorted(removed):
            path = policy.checkout / relative
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                raise ValueError('publication_symlink_denied')
            if not path.is_file():
                raise ValueError('publication_removed_path_missing')
            path.unlink()
        for result in (service.stage('self-development', change_set),):
            self._require(result)
        # --no-renames: a moved document is an add plus a delete; without it git reports the
        # rename (R100) and only the destination, which would break the exact change set.
        staged = git(policy.checkout, 'diff', '--cached', '--no-renames', '--name-only').splitlines()
        if sorted(staged) != change_set:
            raise ValueError('publication_index_mismatch')
        if fingerprint(policy.checkout, sorted(hashes)) != hashes:
            raise ValueError('publication_index_mismatch')
        self._require(service.commit('self-development', item['contract']['commit_message']))
        item['commit_sha'] = git(policy.checkout, 'rev-parse', 'HEAD')
        persist()  # Retry a failed push without creating another commit.

    @staticmethod
    def _require(result):
        if not result.ok:
            error = result.error
            code = error.code if error else 'failed'
            detail = (getattr(error, 'message', '') or '').strip()
            raise ValueError('publication_' + code + ((': ' + detail[:160]) if detail else ''))
