"""Closed Git grants and publication failure paths, fully headless."""
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.services.self_development.publisher import VerifiedPublicationPolicy


def grant():
    policy = SimpleNamespace(assert_current=lambda: None, value={'branch': 'ananta/self-development'})
    verified = []
    return VerifiedPublicationPolicy(policy, ['tests/test_one.py'], 'test(autopilot): cover one case',
                                     lambda: verified.append(True)), verified


def test_publication_grant_is_bound_to_exact_explicit_stage_paths():
    app = Flask(__name__)
    service, verified = grant()
    with app.app_context():
        exact = service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py']})
        broader = service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py', 'agent/auth.py']})
    assert exact.allowed
    assert not broader.allowed
    assert len(verified) == 2


def test_mandatory_human_gate_prevents_automatic_publication():
    app = Flask(__name__)
    app.config['AGENT_CONFIG'] = {'approval_lifecycle': {'human_required_tools': ['git.commit']}}
    service, _ = grant()
    with app.app_context():
        decision = service.authorize('git.commit', 'commit', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'message': 'test(autopilot): cover one case'})
    assert not decision.allowed


def test_publication_revalidates_evidence_before_each_action():
    app = Flask(__name__)
    service, _ = grant()

    def revoked():
        raise ValueError('run_binding_revoked')

    service.verify = revoked
    with app.app_context(), pytest.raises(ValueError, match='run_binding_revoked'):
        service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py']})


def test_global_ops_deny_is_not_overridden():
    app = Flask(__name__)
    app.config['AGENT_CONFIG'] = {'ops': {'mutating_default': 'deny'}}
    # Ops configuration has an established dedicated namespace.
    from agent.services.ops_policy_service import OpsPolicyService
    service, _ = grant()
    with app.app_context():
        original = OpsPolicyService._config
        try:
            OpsPolicyService._config = lambda self: {'mutating_default': 'deny'}
            decision = service.authorize('git.stage', 'stage', target_id='self-development', approval_id=None,
                arguments={'workspace_id': 'self-development', 'paths': ['tests/test_one.py']})
        finally:
            OpsPolicyService._config = original
    assert not decision.allowed


# --- publication on a moved remote branch -------------------------------------------


def _git(root, *args):
    from agent.services.self_development.workspace import git as run
    return run(root, *args)


def _repos(tmp_path):
    """A bare remote plus the closed checkout on the publication branch."""
    import subprocess

    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-b', 'ananta/self-development', str(remote)],
                   check=True, capture_output=True)
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-b', 'ananta/self-development')
    _git(checkout, 'config', 'user.name', 'Test')
    _git(checkout, 'config', 'user.email', 'test@example.com')
    _git(checkout, 'remote', 'add', 'origin', str(remote))
    (checkout / 'keep.txt').write_text('base\n', encoding='utf-8')
    _git(checkout, 'add', '--', 'keep.txt')
    _git(checkout, 'commit', '-m', 'chore: base')
    _git(checkout, 'push', 'origin', 'HEAD:refs/heads/ananta/self-development')
    return remote, checkout


def _remote_commit(tmp_path, remote, name, path, content, message):
    """Simulate the operator merging outside work into the publication branch."""
    import subprocess

    work = tmp_path / name
    subprocess.run(['git', 'clone', '-b', 'ananta/self-development', str(remote), str(work)],
                   check=True, capture_output=True)
    _git(work, 'config', 'user.name', 'Else')
    _git(work, 'config', 'user.email', 'else@example.com')
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    _git(work, 'add', '--', path)
    _git(work, 'commit', '-m', message)
    _git(work, 'push', 'origin', 'HEAD:refs/heads/ananta/self-development')
    return _git(work, 'rev-parse', 'HEAD')


def _seal(tmp_path, task_id, files):
    import hashlib

    seal = tmp_path / 'private' / 'sealed' / task_id
    hashes = {}
    for relative, content in files.items():
        target = seal / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        hashes[relative] = hashlib.sha256(content).hexdigest()
    return hashes


def _policy(root, checkout, remote_url):
    return SimpleNamespace(
        root=root, checkout=checkout, assert_current=lambda: None,
        value={'branch': 'ananta/self-development', 'remote_url': str(remote_url),
               'github_token_file': str(root / 'token')})


class _Service:
    """Closed Git ops over real git: the integration logic runs end to end."""

    def __init__(self, checkout):
        self.checkout = checkout
        self.pushes = 0

    def fetch(self, workspace_id, *, remote=None, branch=None, credential_ref=None, approval_id=None):
        if branch:
            _git(self.checkout, 'fetch', remote, branch)
        else:
            _git(self.checkout, 'fetch', remote)
        return SimpleNamespace(ok=True, error=None)

    def stage(self, workspace_id, paths):
        _git(self.checkout, 'add', '--', *paths)
        return SimpleNamespace(ok=True, error=None)

    def commit(self, workspace_id, message):
        _git(self.checkout, 'commit', '-m', message)
        return SimpleNamespace(ok=True, error=None)

    def push(self, workspace_id, *, remote=None, branch=None, credential_ref=None, approval_id=None):
        self.pushes += 1
        try:
            _git(self.checkout, 'push', remote, f'HEAD:refs/heads/{branch}')
        except ValueError as exc:
            return SimpleNamespace(ok=False, error=SimpleNamespace(code='git_command_failed', message=str(exc)))
        return SimpleNamespace(ok=True, error=None)


def _publisher(policy, service):
    from agent.services.self_development.publisher import Publisher
    return Publisher(policy, service_factory=lambda **kwargs: service)


def _item(task_id, hashes, revision, removed=()):
    return {'task_id': task_id, 'hashes': hashes, 'removed': list(removed), 'revision': revision,
            'status': 'running', 'todo_id': 'T-0',
            'contract': {'commit_message': 'feat(ctx): add text budget clamp helper'}}


def test_publication_adopts_a_moved_remote_branch_before_landing(tmp_path):
    remote, checkout = _repos(tmp_path)
    external = _remote_commit(tmp_path, remote, 'elsewhere', 'extra.txt', 'external\n', 'feat: external merge')
    hashes = _seal(tmp_path, 'task-1', {'agent/text_budget.py': b'clamp\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-1', hashes, _git(checkout, 'rev-parse', 'HEAD'))

    commit_sha = _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert item['status'] == 'published'
    assert item['published_base'] == external
    assert _git(checkout, 'show', 'HEAD:agent/text_budget.py') == 'clamp'
    assert _git(checkout, 'show', 'HEAD:extra.txt') == 'external'
    from agent.services.self_development.workspace import is_ancestor
    assert is_ancestor(checkout, external, commit_sha)


def test_publication_replays_an_unpushed_chain_onto_the_tip(tmp_path):
    remote, checkout = _repos(tmp_path)
    # An earlier publication commit never reached the remote (its push failed).
    (checkout / 'agent').mkdir()
    (checkout / 'agent' / 'a.py').write_text('a\n', encoding='utf-8')
    _git(checkout, 'add', '--', 'agent/a.py')
    _git(checkout, 'commit', '-m', 'feat: first chain commit')
    chain_head = _git(checkout, 'rev-parse', 'HEAD')
    external = _remote_commit(tmp_path, remote, 'elsewhere', 'extra.txt', 'external\n', 'feat: external merge')
    hashes = _seal(tmp_path, 'task-2', {'agent/b.py': b'b\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-2', hashes, chain_head)

    commit_sha = _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert item['status'] == 'published'
    assert _git(checkout, 'show', 'HEAD:agent/a.py') == 'a'
    assert _git(checkout, 'show', 'HEAD:agent/b.py') == 'b'
    assert _git(checkout, 'show', 'HEAD:extra.txt') == 'external'
    from agent.services.self_development.workspace import is_ancestor
    assert is_ancestor(checkout, external, commit_sha)
    assert is_ancestor(checkout, chain_head, commit_sha)


def test_publication_refuses_to_overwrite_remote_changes(tmp_path):
    remote, checkout = _repos(tmp_path)
    (checkout / 'agent').mkdir()
    (checkout / 'agent' / 'text_budget.py').write_text('base\n', encoding='utf-8')
    _git(checkout, 'add', '--', 'agent/text_budget.py')
    _git(checkout, 'commit', '-m', 'feat: base clamp')
    revision = _git(checkout, 'rev-parse', 'HEAD')
    _git(checkout, 'push', 'origin', 'HEAD:refs/heads/ananta/self-development')
    _remote_commit(tmp_path, remote, 'elsewhere', 'agent/text_budget.py', 'remote-change\n', 'feat: remote change')
    hashes = _seal(tmp_path, 'task-3', {'agent/text_budget.py': b'mine\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-3', hashes, revision)

    with pytest.raises(ValueError, match='publication_base_changed_files'):
        _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert service.pushes == 0
    assert _git(checkout, 'show', 'HEAD:agent/text_budget.py') == 'remote-change'


def test_publication_rebase_conflict_fails_closed(tmp_path):
    remote, checkout = _repos(tmp_path)
    (checkout / 'agent').mkdir()
    (checkout / 'agent' / 'text_budget.py').write_text('chain\n', encoding='utf-8')
    _git(checkout, 'add', '--', 'agent/text_budget.py')
    _git(checkout, 'commit', '-m', 'feat: chain clamp')
    chain_head = _git(checkout, 'rev-parse', 'HEAD')
    _remote_commit(tmp_path, remote, 'elsewhere', 'agent/text_budget.py', 'remote-change\n', 'feat: remote change')
    hashes = _seal(tmp_path, 'task-4', {'agent/text_budget.py': b'chain+mine\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-4', hashes, chain_head)

    with pytest.raises(ValueError, match='publication_merge_conflict'):
        _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert service.pushes == 0
    assert _git(checkout, 'status', '--porcelain') == ''
    assert _git(checkout, 'rev-parse', 'HEAD') == chain_head


def test_publication_completes_an_already_pushed_commit_without_rebuilding(tmp_path):
    remote, checkout = _repos(tmp_path)
    hashes = _seal(tmp_path, 'task-5', {'agent/text_budget.py': b'clamp\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-5', hashes, _git(checkout, 'rev-parse', 'HEAD'))
    _publisher(policy, service).publish(item, lambda: None, lambda: None)
    pushed = item['commit_sha']
    count = _git(checkout, 'rev-list', '--count', 'HEAD')

    # The push landed but the run died before the bookkeeping was persisted.
    item.update(status='running')
    commit_sha = _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert item['status'] == 'published'
    assert commit_sha == pushed
    assert _git(checkout, 'rev-list', '--count', 'HEAD') == count


def test_publication_records_the_carrying_commit_when_the_chain_carries_the_change(tmp_path):
    remote, checkout = _repos(tmp_path)
    (checkout / 'agent').mkdir()
    (checkout / 'agent' / 'text_budget.py').write_text('chain\n', encoding='utf-8')
    _git(checkout, 'add', '--', 'agent/text_budget.py')
    _git(checkout, 'commit', '-m', 'feat: first chain commit')
    base = _git(checkout, 'rev-parse', 'HEAD')
    (checkout / 'agent' / 'text_budget.py').write_text('chain+mine\n', encoding='utf-8')
    _git(checkout, 'add', '--', 'agent/text_budget.py')
    _git(checkout, 'commit', '-m', 'feat(ctx): add text budget clamp helper')
    stuck = _git(checkout, 'rev-parse', 'HEAD')
    external = _remote_commit(tmp_path, remote, 'elsewhere', 'extra.txt', 'external\n', 'feat: external merge')
    hashes = _seal(tmp_path, 'task-6', {'agent/text_budget.py': b'chain+mine\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-6', hashes, base, )
    item['commit_sha'] = stuck

    commit_sha = _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert item['status'] == 'published'
    # The merge keeps the stuck commit's identity: the audit record stays truthful.
    assert commit_sha == stuck
    assert _git(checkout, 'show', 'HEAD:agent/text_budget.py') == 'chain+mine'
    assert _git(checkout, 'show', 'HEAD:extra.txt') == 'external'
    from agent.services.self_development.workspace import is_ancestor
    assert is_ancestor(checkout, external, _git(checkout, 'rev-parse', 'HEAD'))


def test_publication_grant_is_bound_to_exact_fetch_arguments():
    app = Flask(__name__)
    service, _ = grant()
    with app.app_context():
        exact = service.authorize('git.fetch', 'fetch', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'remote': 'origin',
                       'branch': 'ananta/self-development',
                       'credential_ref': 'secret://self-development/github'})
        other = service.authorize('git.fetch', 'fetch', target_id='self-development', approval_id=None,
            arguments={'workspace_id': 'self-development', 'remote': 'origin',
                       'branch': 'setup/local-self-development',
                       'credential_ref': 'secret://self-development/github'})
    assert exact.allowed
    assert not other.allowed


def test_publication_recovery_is_judged_by_state_not_reason_text(tmp_path):
    from agent.services.self_development.coordinator import DevelopmentCoordinator

    reviewed = SimpleNamespace(policy=SimpleNamespace(root=tmp_path,
        value={'quality_gates': {'require_acceptance_review': True}}))
    item = {'task_id': 'task-7', 'status': 'failed', 'hashes': {'a.py': 'x'},
            'contract': {'commit_message': 'm'}, 'acceptance_review': {'verdict': 'approved'},
            'reason': 'self_development_git_command_failed:rev-parse'}
    # A lost seal means the change cannot be rebuilt: fall back to the full re-run path.
    assert not DevelopmentCoordinator._publication_retryable(reviewed, item)
    (tmp_path / 'sealed' / 'task-7').mkdir(parents=True)
    assert DevelopmentCoordinator._publication_retryable(reviewed, item)
    # A rejected or missing review never publishes; only a re-run can make progress.
    unreviewed = {**item, 'acceptance_review': None}
    assert not DevelopmentCoordinator._publication_retryable(reviewed, unreviewed)
    permissive = SimpleNamespace(policy=SimpleNamespace(root=tmp_path, value={}))
    assert DevelopmentCoordinator._publication_retryable(permissive, unreviewed)
    assert not DevelopmentCoordinator._publication_retryable(
        reviewed, {'task_id': 'task-9', 'status': 'failed'})


def test_publication_refuses_a_checkout_on_an_unrelated_history(tmp_path):
    import subprocess

    remote, checkout = _repos(tmp_path)
    foreign = tmp_path / 'foreign'
    subprocess.run(['git', 'init', '-b', 'ananta/self-development', str(foreign)],
                   check=True, capture_output=True)
    _git(foreign, 'config', 'user.name', 'Foreign')
    _git(foreign, 'config', 'user.email', 'foreign@example.com')
    (foreign / 'f.txt').write_text('f\n', encoding='utf-8')
    _git(foreign, 'add', '--', 'f.txt')
    _git(foreign, 'commit', '-m', 'feat: foreign line')
    hashes = _seal(tmp_path, 'task-10', {'agent/text_budget.py': b'clamp\n'})
    policy = _policy(tmp_path / 'private', checkout, remote)
    service = _Service(checkout)
    item = _item('task-10', hashes, _git(foreign, 'rev-parse', 'HEAD'))

    with pytest.raises(ValueError, match='publication_checkout_moved'):
        _publisher(policy, service).publish(item, lambda: None, lambda: None)

    assert service.pushes == 0


def test_prepare_retry_carries_the_worker_pin(tmp_path):
    import time

    from agent.services.self_development.retries import prepare_retry

    policy = SimpleNamespace(root=tmp_path, assert_current=lambda: None,
        value={'max_attempts': 2, 'max_seconds': 0, 'max_tasks': 0, 'team_id': 't'})
    state = {'session': {'id': 's', 'enabled': True,
                         'expires_at': time.time() + 3600, 'dispatched': 0}}
    ingested = []
    queue = SimpleNamespace(ingest_task=lambda **kwargs: ingested.append(kwargs))
    item = {'task_id': 'old', 'status': 'failed', 'contract': {'commit_message': 'm'},
            'title': 't', 'description': 'd', 'todo_id': 'T',
            'assigned_agent_url': 'http://ai-agent-alpha:5000'}

    assert prepare_retry(item, policy, state, queue) is True

    assert item['task_id'] != 'old'
    assert ingested[0]['extra_fields']['assigned_agent_url'] == 'http://ai-agent-alpha:5000'
    unpinned = {'task_id': 'old-2', 'status': 'failed', 'contract': {'commit_message': 'm'},
                'title': 't', 'description': 'd', 'todo_id': 'T-2'}
    assert prepare_retry(unpinned, policy, state, queue) is True
    assert 'assigned_agent_url' not in ingested[1]['extra_fields']
