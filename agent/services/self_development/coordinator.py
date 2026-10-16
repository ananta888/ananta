"""Hub task ingress and bounded execution; scheduling stays in Autopilot."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict

from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest, EvidenceGateSourceAdmission, canonical_evidence_digest,
    get_hub_evidence_gate_service,
)
from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
from agent.services.self_development.catalog import catalog, import_document
from agent.services.self_development.publisher import Publisher
from agent.services.self_development.state import Journal
from agent.services.self_development.workspace import export_checkout, git, seal_changes
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import update_local_task_status


class DevelopmentCoordinator:
    def __init__(self, policy, *, gate=None, publisher=None, queue=None, registry=None):
        self.policy = policy
        self.journal = Journal(policy.root)
        self.gate = gate or get_hub_evidence_gate_service()
        self.publisher = publisher or Publisher(policy)
        self.queue = queue or get_task_queue_service()
        self.registry = registry or get_hub_evidence_registry_service()

    def import_tasks(self, document=None):
        self.policy.assert_current()
        report = catalog(self.policy.checkout, self.policy) if document is None else {
            'items': import_document(document, 'user-submission', self.policy), 'errors': []}
        with self.journal.locked():
            state = self.journal.read()
            added = 0
            for item in report['items']:
                old = state['items'].get(item['key'])
                if old:
                    if old['status'] != 'blocked' or old.get('contract') == item.get('contract'):
                        continue
                    item['task_id'] = old['task_id']
                else:
                    item['task_id'] = str(uuid.uuid4())
                # Queue dependency links are resolved only from the same admitted document.
                unresolved = []
                dependency_ids = []
                for dependency in item['dependencies']:
                    match = next((r for r in report['items'] if r['source'] == item['source']
                                  and r['todo_id'] == dependency), None)
                    existing = state['items'].get(match['key']) if match else None
                    if existing and existing['status'] == 'published':
                        dependency_ids.append(existing['task_id'])
                    else:
                        unresolved.append(dependency)
                if unresolved:
                    item['status'], item['reason'] = 'blocked', 'dependencies_not_published'
                state['items'][item['key']] = item
                self.queue.ingest_task(
                    task_id=item['task_id'], status='paused',
                    title=item['title'], description=item['description'], created_by='self-development-hub',
                    source='system', team_id=self.policy.value['team_id'], tags=['self-development', item['todo_id']],
                    event_details={'source_file': item['source'], 'reason': item['reason']},
                    extra_fields={'task_kind': 'coding', 'required_capabilities': ['coding', 'testing',
                        'workflow.adapter.native'], 'depends_on': dependency_ids})
                added += 1
            self.journal.write(state)
        return {'added': added, 'catalog_errors': report['errors'], **self.status()}

    def status(self):
        state = self.journal.read()
        items = list(state['items'].values())
        return {'enabled': True, 'automatic_publication': True, 'branch': self.policy.value['branch'],
            'session': state.get('session'), 'counts': {status: sum(i['status'] == status for i in items)
                for status in sorted({i['status'] for i in items})}, 'items': items}

    def start(self):
        with self.journal.locked():
            self.policy.assert_current()
            state = self.journal.read()
            session = state.get('session', {})
            if session.get('enabled') and session.get('expires_at', 0) > time.time():
                (self.policy.root / 'stop').unlink(missing_ok=True)
                for item in state['items'].values():
                    if item['status'] == 'ready':
                        update_local_task_status(item['task_id'], 'todo')
                return self.status()
            (self.policy.root / 'stop').unlink(missing_ok=True)
            state['session'] = {'id': str(uuid.uuid4()), 'enabled': True,
                'expires_at': time.time() + min(21600, self.policy.value.get('max_seconds', 1800)), 'dispatched': 0}
            self.journal.write(state)
            for item in state['items'].values():
                if item['status'] == 'ready':
                    update_local_task_status(item['task_id'], 'todo')
        return self.status()

    def stop(self):
        # Revocation is independent of the long-lived dispatch lock.
        (self.policy.root / 'stop').write_text('operator_stop')
        return {'stopping': True, 'reason': 'publication_revoked'}

    def manages(self, task_id):
        return any(i['task_id'] == task_id for i in self.journal.read()['items'].values())

    def dispatch(self, task, worker, execute):
        from agent.routes.tasks.autopilot_task_dispatcher_helpers import TaskDispatchResult
        with self.journal.locked():
            state = self.journal.read()
            item = next(i for i in state['items'].values() if i['task_id'] == task.id)
            try:
                self._check_session(state)
                if item['status'] != 'ready':
                    raise ValueError('development_task_not_ready')
                state['session']['dispatched'] += 1
                item['status'] = 'running'
                self.journal.write(state)
                result = self._execute(state, item, task, worker, execute)
                if item['status'] != 'published':
                    result.completed, result.failed = False, True
                    result.failure_type = 'development_not_published'
                return result
            except Exception as exc:
                item['status'] = 'publication_failed' if item.get('outcome', {}).get('verified') else 'failed'
                item['reason'] = str(exc)[:240]
                self.journal.write(state)
                update_local_task_status(task.id, 'failed', last_output=item['reason'])
                return TaskDispatchResult(task.id, dispatched=True, failed=True,
                    failure_type='self_development_failed')

    def _check_session(self, state):
        self.policy.assert_current()
        session = state.get('session') or {}
        if ((self.policy.root / 'stop').exists() or not session.get('enabled')
                or time.time() >= session.get('expires_at', 0)
                or session.get('dispatched', 0) >= self.policy.value['max_tasks']):
            raise ValueError('development_session_stopped_or_budget_exhausted')

    def _execute(self, state, item, task, worker, execute):
        policy = self.policy
        profile = policy.authorize_contract(item['contract'])
        item['revision'] = git(policy.checkout, 'rev-parse', 'HEAD')
        item['worker_url'] = str(worker.url)
        workspace = policy.workspaces / ('self-dev-' + task.id)
        baseline = export_checkout(policy.checkout, workspace)
        manifest = {'revision': item['revision'], 'files': baseline, 'task': item,
                    'profile': profile, 'policy_digest': policy.digest}
        admission = policy.root / ('source-' + task.id + '.json')
        admission.write_text(json.dumps(manifest, sort_keys=True))
        admission.chmod(0o400)
        digest = canonical_evidence_digest(manifest)
        request = EvidenceGateRequest(tenant_id=policy.value['tenant_id'], project_id='ananta-self-development',
            task_id=task.id, assignment_id=str(uuid.uuid4()), dispatch_lease_id=str(uuid.uuid4()),
            repository_revision=item['revision'], input_digest=digest,
            execution_profile_digest=canonical_evidence_digest(profile),
            environment_digest=canonical_evidence_digest({'worker': str(worker.url), 'profile': profile}),
            evidence_scope='local', required_scope='local', idempotency_key='self-development:' + task.id,
            sources=(EvidenceGateSourceAdmission('repository',
                canonical_evidence_digest({'revision': item['revision'], 'todo': item['source']}),
                digest, policy.digest),))
        dispatch_result = None

        def run(projection):
            nonlocal dispatch_result
            context = {'workspace': {'output_dir': str(workspace), 'scope_key': 'self-development',
                'git_workspace': {'enabled': False}}, 'artifact_sync': {'enabled': True, 'sync_to_hub': True},
                'hub_evidence_assignment': dict(projection)}
            description = (item['description'] + '\nOnly change these files: '
                + ', '.join(item['contract']['files']) + '\nRun exactly: ' + profile['command']
                + '\nUse test.run and finish only after exit code 0. Never commit, push, create tasks, '
                'change policy, Git metadata, or contact workers. Hub handles publication.')
            update_local_task_status(task.id, task.status,
                worker_execution_context=context, description=description)
            task.worker_execution_context = context
            task.description = description
            item['assignment'] = dict(projection)
            self.journal.write(state)
            dispatch_result = execute()
            if not dispatch_result.completed:
                return {'passed': False, 'reason_code': 'worker_execution_failed'}
            self._check_live(state)
            self._verify_worker_result(task.id, str(worker.url), profile['command'])
            item['hashes'] = seal_changes(workspace, baseline, item['contract']['files'],
                policy.root / 'sealed' / task.id)
            return {'passed': True, 'hashes': item['hashes'], 'worker_url': str(worker.url),
                    'assignment_digest': canonical_evidence_digest(dict(projection))}

        outcome = self.gate.execute(request, run)
        item['outcome'] = asdict(outcome)
        self.journal.write(state)
        if not outcome.passed or not outcome.verified:
            raise ValueError('development_evidence_gate_failed:' + outcome.reason_code)
        from agent.services.self_development.completion import seal_completion
        seal_completion(policy, item)
        self.journal.write(state)
        self.publisher.publish(item, lambda: self._verify(state, item), lambda: self.journal.write(state))
        self._release_dependencies(state)
        return dispatch_result

    def _release_dependencies(self, state):
        for item in state['items'].values():
            if item.get('reason') != 'dependencies_not_published' or not item.get('contract'):
                continue
            if item['original_status'] in {'blocked', 'blocked_by_dependency', 'paused', 'needs_review'}:
                continue
            completed = {i['todo_id'] for i in state['items'].values()
                         if i['source'] == item['source'] and i['status'] == 'published'}
            if set(item['dependencies']).issubset(completed):
                item['status'], item['reason'] = 'ready', None
                update_local_task_status(item['task_id'], 'todo')
        self.journal.write(state)

    @staticmethod
    def _verify_worker_result(task_id, worker_url, command):
        from agent.services.repository_registry import get_repository_registry
        current = get_repository_registry().task_repo.get_by_id(task_id)
        proposal = dict(getattr(current, 'last_proposal', None) or {})
        if (current is None or current.status != 'completed' or current.last_exit_code != 0
                or str(current.assigned_agent_url or '').rstrip('/') != worker_url.rstrip('/')
                or str(proposal.get('command') or '').strip() != command.strip()):
            raise ValueError('worker_verification_profile_mismatch')

    def _check_live(self, state):
        self.policy.assert_current()
        if (self.policy.root / 'stop').exists() or time.time() >= state['session']['expires_at']:
            raise ValueError('development_lease_revoked')

    def _verify(self, state, item):
        self._check_live(state)
        outcome = item['outcome']
        assignment = item['assignment']
        self.registry.require_assignment_result(tenant_id=self.policy.value['tenant_id'],
            project_id='ananta-self-development', projection=assignment, task_id=item['task_id'],
            assignment_id=assignment['assignment_id'], dispatch_lease_id=assignment['dispatch_lease_id'],
            result_digest=outcome['result_digest'])
        verification = self.registry.verify_release_binding(tenant_id=self.policy.value['tenant_id'],
            project_id='ananta-self-development', run_id=outcome['run_id'], required_scope='local',
            task_id=item['task_id'], repository_revision=item['revision'], source_ids=tuple(outcome['source_ids']))
        if not outcome['passed'] or not outcome['verified'] or not verification.verified:
            raise ValueError('publication_evidence_unverified')
