"""Hub task ingress and bounded execution; scheduling stays in Autopilot."""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict

from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    canonical_evidence_digest,
    get_hub_evidence_gate_service,
)
from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
from agent.services.self_development.budget import (
    session_active,
    session_deadline,
    task_budget_exhausted,
)
from agent.services.self_development.catalog import catalog, import_document
from agent.services.self_development.publisher import Publisher
from agent.services.self_development.state import Journal
from agent.services.self_development.workspace import (
    export_checkout,
    git,
    seal_changes,
    write_scope_manifest,
)
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import update_local_task_status


class DevelopmentCoordinator:
    def __init__(self, policy, *, gate=None, publisher=None, queue=None, registry=None, planner=None):
        self.policy = policy
        self.journal = Journal(policy.root)
        self.gate = gate or get_hub_evidence_gate_service()
        self.publisher = publisher or Publisher(policy)
        self.queue = queue or get_task_queue_service()
        self.registry = registry or get_hub_evidence_registry_service()
        self.planner = planner

    def sync_todo_folder(self):
        """Synchronise the tracked TODO folder (deterministic bookkeeping).

        Two kinds of bookkeeping, both publication-gated and model-free: tasks whose declared
        dependencies are all done are released from ``blocked``, and documents that are fully
        worked off are archived. Called before the catalog is built and at Hub start.
        """
        from agent.services.self_development.completion import (
            archive_completed_sources,
            unblock_satisfied_sources,
        )

        publish = lambda item: self.publisher.publish(item, lambda: None, lambda: None)
        unblocked = unblock_satisfied_sources(self.policy, publish=publish)
        archived = archive_completed_sources(self.policy, publish=publish)
        return {'unblocked': unblocked, 'archived': archived}

    def import_tasks(self, document=None):
        self.policy.assert_current()
        if document is None:
            self.sync_todo_folder()
        report = catalog(self.policy.checkout, self.policy) if document is None else {
            'items': import_document(document, 'user-submission', self.policy), 'errors': []}
        with self.journal.locked():
            state = self.journal.read()
            added = 0
            for item in report['items']:
                # Planning opt-in: contract-less legacy TODOs become eligible and
                # receive a Hub-validated contract at dispatch time.
                plannable = (self.policy.planning_enabled() and item['status'] == 'blocked'
                             and str(item.get('reason') or '').startswith('needs_'))
                if plannable:
                    item['status'], item['reason'] = 'ready', 'contract_pending'
                old = state['items'].get(item['key'])
                if old:
                    re_admit = plannable and old['status'] == 'blocked' and not old.get('contract')
                    # Any item that never received a contract (planning failed or
                    # was blocked) stays recoverable on the next import instead
                    # of wedging forever. Items that failed *after* a contract was
                    # admitted keep their contract and are not silently reopened.
                    recoverable = (self.policy.planning_enabled()
                                   and not old.get('contract')
                                   and old['status'] in {'blocked', 'failed'})
                    if not (re_admit or recoverable) and (
                            old['status'] != 'blocked' or old.get('contract') == item.get('contract')):
                        continue
                    item['task_id'] = old['task_id']
                else:
                    item['task_id'] = str(uuid.uuid4())
                # Queue dependency links are resolved only from the same admitted document.
                skip_unpublished = self.policy.value.get('skip_unpublished_dependencies') is True
                unresolved = []
                external = []
                dependency_ids = []
                for dependency in item['dependencies']:
                    match = next((r for r in report['items'] if r['source'] == item['source']
                                  and r['todo_id'] == dependency), None)
                    existing = state['items'].get(match['key']) if match else None
                    if existing and existing['status'] == 'published':
                        dependency_ids.append(existing['task_id'])
                    else:
                        unresolved.append(dependency)
                        if match is None:
                            external.append(dependency)
                if unresolved and item['status'] == 'ready':
                    if skip_unpublished and external:
                        item['status'], item['reason'] = 'skipped', 'dependency_not_published'
                    else:
                        item['status'], item['reason'] = 'blocked', 'dependencies_not_published'
                state['items'][item['key']] = item
                self.queue.ingest_task(
                    task_id=item['task_id'], status='paused',
                    title=item['title'], description=item['description'], created_by='self-development-hub',
                    source='system', team_id=self.policy.value['team_id'], tags=['self-development', item['todo_id']],
                    event_details={'source_file': item['source'], 'reason': item['reason']},
                    extra_fields={'task_kind': 'coding', 'required_capabilities': ['coding', 'testing',
                        'workflow.adapter.native', 'codecompass'], 'depends_on': dependency_ids})
                added += 1
            self._sweep_dependencies(state)
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
            if session_active(self.policy.value, session, time.time()):
                (self.policy.root / 'stop').unlink(missing_ok=True)
                self._sweep_dependencies(state)
                self.journal.write(state)
                for item in state['items'].values():
                    if item['status'] == 'ready':
                        update_local_task_status(item['task_id'], 'todo')
                return self.status()
            (self.policy.root / 'stop').unlink(missing_ok=True)
            state['session'] = {'id': str(uuid.uuid4()), 'enabled': True,
                'expires_at': session_deadline(self.policy.value, time.time()), 'dispatched': 0}
            self.journal.write(state)
            for item in state['items'].values():
                if item['status'] == 'ready':
                    update_local_task_status(item['task_id'], 'todo')
        return self.status()

    def stop(self):
        # Revocation is independent of the long-lived dispatch lock.
        (self.policy.root / 'stop').write_text('operator_stop')
        return {'stopping': True, 'reason': 'publication_revoked'}

    def retry(self):
        from agent.services.self_development.retries import prepare_retry
        with self.journal.locked():
            state = self.journal.read()
            self._check_session(state)
            count = sum(prepare_retry(item, self.policy, state, self.queue)
                        for item in state['items'].values() if item['status'] == 'failed')
            self.journal.write(state)
        return {'retried': count, **self.status()}

    def manages(self, task_id):
        return any(i['task_id'] == task_id or any(a['task_id'] == task_id for a in i.get('attempts', []))
                   for i in self.journal.read()['items'].values())

    def dispatch(self, task, worker, execute):
        from agent.ports.task_dispatch import TaskDispatchResult
        with self.journal.locked():
            state = self.journal.read()
            item = next((i for i in state['items'].values() if i['task_id'] == task.id), None)
            if item is None:
                update_local_task_status(task.id, 'cancelled',
                    event_type='self_development_stale_attempt_rejected')
                return TaskDispatchResult(task.id, failed=True, failure_type='stale_development_attempt')
            try:
                self._check_session(state)
                if item['status'] != 'ready':
                    raise ValueError('development_task_not_ready')
                if not item.get('contract'):
                    self._admit_planned_contract(state, item)
                state['session']['dispatched'] += 1
                item['status'] = 'running'
                self.journal.write(state)
                result = self._execute(state, item, task, worker, execute)
                if item['status'] != 'published':
                    result.completed, result.failed = False, True
                    result.failure_type = 'development_not_published'
                return result
            except Exception as exc:
                was_running = item['status'] == 'running'
                item['status'] = 'publication_failed' if item.get('outcome', {}).get('verified') else 'failed'
                item['reason'] = str(exc)[:240]
                self.journal.write(state)
                update_local_task_status(task.id, 'failed', last_output=item['reason'])
                if was_running:
                    from agent.services.self_development.retries import prepare_retry
                    prepare_retry(item, self.policy, state, self.queue)
                    self.journal.write(state)
                return TaskDispatchResult(task.id, dispatched=True, failed=True,
                    failure_type='self_development_failed')

    def _check_session(self, state):
        self.policy.assert_current()
        session = state.get('session') or {}
        if ((self.policy.root / 'stop').exists()
                or not session_active(self.policy.value, session, time.time())
                or task_budget_exhausted(self.policy.value, session)):
            raise ValueError('development_session_stopped_or_budget_exhausted')

    @staticmethod
    def _item_tokens(item):
        text = ' '.join(str(item.get(field, '')) for field in ('todo_id', 'title', 'description')).lower()
        return sorted({token for token in re.findall(r'[a-z0-9_]{4,}', text)})[:12]

    def _planning_envelope(self, item):
        from agent.services.self_development.planner import PlanningEnvelope
        roots = tuple(str(r).rstrip('/') for r in self.policy.value.get('allowed_roots', ()))
        allowed = [f for f in git(self.policy.checkout, 'ls-files').splitlines()
                   if any(f.startswith(root + '/') for root in roots)]
        tokens = self._item_tokens(item)
        matches = [f for f in allowed if any(token in f.lower() for token in tokens)]
        return PlanningEnvelope.from_policy(self.policy, (matches or allowed)[:400])

    def _admit_planned_contract(self, state, item):
        from agent.services.self_development.contracts import task_contract
        if self.planner is None:
            raise ValueError('development_planner_unavailable')
        envelope = self._planning_envelope(item)
        max_attempts = int((self.policy.value.get('planning') or {}).get('max_attempts', 2))
        error = None
        for _ in range(max(1, min(max_attempts, 5))):
            proposal = self.planner.propose(item=item, envelope=envelope, error=error)
            try:
                execution = {**proposal, 'completion_rule': 'profile_passed'}
                contract = task_contract({'execution': execution})
                if contract['test_profile'] not in envelope.allowed_test_profiles:
                    raise ValueError('planned_test_profile_not_authorized')
                if envelope.scope_of(contract['commit_message']) not in envelope.commit_scopes:
                    raise ValueError('planned_commit_scope_denied')
                if len(contract['files']) > envelope.max_files:
                    raise ValueError('planned_scope_too_broad')
                self.policy.authorize_contract(contract)
            except ValueError as exc:
                error = str(exc)[:200]
                continue
            item['contract'] = contract
            item['source_execution'] = execution
            item['planned'] = True
            item['reason'] = 'planned_contract_admitted'
            self.journal.write(state)
            return
        raise ValueError('planned_admission_failed:' + (error or 'unknown'))

    def _execute(self, state, item, task, worker, execute):
        policy = self.policy
        profile = policy.authorize_contract(item['contract'])
        item['revision'] = git(policy.checkout, 'rev-parse', 'HEAD')
        item['worker_url'] = str(worker.url)
        workspace = policy.workspaces / ('self-dev-' + task.id)
        baseline = export_checkout(policy.checkout, workspace)
        write_scope_manifest(workspace, item['contract']['files'])
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
            if item.get('reason') not in {'dependencies_not_published', 'dependency_not_published'}:
                continue
            if item['original_status'] in {'blocked', 'blocked_by_dependency', 'paused', 'needs_review'}:
                continue
            completed = {i['todo_id'] for i in state['items'].values()
                         if i['source'] == item['source'] and i['status'] == 'published'}
            if set(item['dependencies']).issubset(completed):
                item['status'], item['reason'] = 'ready', None
                update_local_task_status(item['task_id'], 'todo')
        self._sweep_dependencies(state)
        self.journal.write(state)

    def _sweep_dependencies(self, state):
        """Mark tasks whose prerequisite can never publish as ``skipped``.

        A dependency is dead when it is unknown to the admitted document set or
        its own task is already terminally negative. Live prerequisites
        (``ready``/``running``/``published``) keep the dependent blocked so an
        in-order chain still completes.
        """
        by_source: dict[str, dict[str, dict]] = {}
        for item in state['items'].values():
            by_source.setdefault(item['source'], {})[item['todo_id']] = item
        terminal = {'blocked', 'skipped', 'failed'}
        for item in state['items'].values():
            if item.get('reason') != 'dependencies_not_published' or item['status'] != 'blocked':
                continue
            siblings = by_source.get(item['source'], {})
            dead = False
            for dependency in item['dependencies']:
                target = siblings.get(dependency)
                if target is None or target['status'] in terminal:
                    dead = True
                    break
            if dead:
                item['status'], item['reason'] = 'skipped', 'dependency_not_published'
                update_local_task_status(item['task_id'], 'cancelled',
                    event_type='self_development_dependency_deadlocked')

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
        if ((self.policy.root / 'stop').exists()
                or not session_active(self.policy.value, state.get('session'), time.time())):
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
