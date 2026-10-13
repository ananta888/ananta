"""Hub-only retirement issuance and receipt reconciliation in journal transactions."""

import math
import time

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.native_result_retirement import NativeResultRetirementManifestDB, NativeResultRetirementMarkerDB
from agent.db_models.tasks import TaskDB
from agent.repositories.native_result_retirement import retirement_marker_key
from agent.repositories.sqlalchemy_support import require_locked_row, stable_row_id
from agent.services.native_dispatch_binding import native_dispatch_binding_digest
from agent.services.native_dispatch_journal import require_native_dispatch_receipt
from agent.services.native_result_retirement_admission import require_retirable_result
from agent.services.native_result_task_projection import native_task_command, native_task_result
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_result_maintenance import maintenance_input
from ananta_contracts.native_result_retirement import (
    MAX_RETIREMENT_ENTRIES,
    RETIREMENT_CONTEXT_FIELD,
    RETIREMENT_SCHEMA,
    retirement_payload,
    verify_retirement_manifest,
)

MANIFEST_CONTEXT_FIELD = RETIREMENT_CONTEXT_FIELD


class NativeResultRetirementService:
    """One existing Hub Task at a time; no queue, timer or Worker orchestration."""

    def __init__(self, *, repository, authority, keys, policy, clock=time.time):
        self._repository, self._authority, self._keys, self._policy = repository, authority, keys, policy
        self._clock = clock

    def prepare(self, *, task_id, assignment):
        now = self._now()
        with self._repository.transaction() as session:
            task = self._task(session, task_id)
            command = native_task_command(task)
            if task.status not in {'todo', 'created', 'queued', 'pending', 'in_progress'}:
                raise ValueError('native_result_retirement_task_not_executable')
            self._policy(task, command)
            current = self._authority.require_current(session=session, command=command, task=task, now=now)
            if (current.worker_id, current.worker_url, current.assignment_revision) != (
                    assignment.worker_id, assignment.worker_url, assignment.revision):
                raise ValueError('native_result_retirement_current_assignment_denied')
            batch_size, owner = maintenance_input(command.input_data.get('workflow_input'))
            if owner is None or (owner['worker_id'], owner['worker_url']) != (current.worker_id, current.worker_url):
                raise ValueError('native_result_retirement_owner_assignment_denied')
            row = session.get(NativeResultRetirementManifestDB, task_id)
            if row is not None:
                self._repository.require_manifest_row(row, task)
                if (task.worker_execution_context or {}).get(MANIFEST_CONTEXT_FIELD) != row.manifest:
                    raise ValueError('native_result_retirement_task_manifest_denied')
                verify_retirement_manifest(row.manifest, task=task.model_dump(mode='json'), owner=owner,
                                           key_ring=self._keys(), now=now)
                return row.manifest
            if (task.worker_execution_context or {}).get(MANIFEST_CONTEXT_FIELD) is not None:
                raise ValueError('native_result_retirement_unregistered_manifest')
            if session.get(NativeDispatchDB, stable_row_id('native-dispatch', task.tenant_id, task.id)) is not None:
                raise ValueError('native_result_retirement_post_dispatch_issuance_denied')
            entries = []
            for candidate in self._repository.candidates(session=session, tenant_id=task.tenant_id,
                    project_id=task.project_id, owner=owner, exclude_task_id=task.id,
                    limit=min(batch_size, MAX_RETIREMENT_ENTRIES)):
                if self._now() >= command.authorization.expires_at:
                    raise ValueError('native_result_retirement_current_grant_expired')
                try:
                    entry = require_retirable_result(session=session, task_id=candidate, tenant_id=task.tenant_id,
                        project_id=task.project_id, owner=owner, now=now)
                except (ValueError, RuntimeError):
                    continue  # Bounded cursor advances; uncertain slots stay protected.
                if len(entry['task_id'].encode('utf-8')) <= 200:
                    entries.append(entry)
            now = self._now()
            self._policy(task, command)
            self._authority.require_current(session=session, command=command, task=task, now=now)
            payload = retirement_payload({'schema': RETIREMENT_SCHEMA, 'tenant_id': task.tenant_id,
                'project_id': task.project_id, 'maintenance_task_id': task.id,
                'command_digest': native_context_digest(command.to_dict()), 'owner': owner,
                'expires_at': min(now + 60, command.authorization.expires_at), 'entries': entries},
                task=task.model_dump(mode='json'), owner=owner, now=now)
            keys = self._keys()
            if keys.signature_algorithm != 'ed25519':
                raise ValueError('native_result_retirement_public_verification_required')
            key_id, signature = keys.sign(namespace=RETIREMENT_SCHEMA, payload=payload)
            manifest = {**payload, 'key_id': key_id, 'signature': signature}
            task.worker_execution_context = {**task.worker_execution_context, MANIFEST_CONTEXT_FIELD: manifest}
            session.add(task)
            session.add(NativeResultRetirementManifestDB(id=task.id, tenant_id=task.tenant_id,
                project_id=task.project_id, manifest=manifest, manifest_digest=native_context_digest(manifest),
                issued_at=now, updated_at=now))
            return manifest

    def confirm(self, *, task_id):
        now = self._now()
        with self._repository.transaction() as session:
            task = self._task(session, task_id)
            row = require_locked_row(session, NativeResultRetirementManifestDB, task_id,
                                     missing_reason='native_result_retirement_manifest_missing')
            self._repository.require_manifest_row(row, task)
            command = native_task_command(task)
            receipt = require_native_dispatch_receipt(task)
            verify_retirement_manifest(row.manifest, task=task.model_dump(mode='json'), owner=row.manifest['owner'],
                                       key_ring=self._keys(), now=row.issued_at)
            journal = require_locked_row(session, NativeDispatchDB, stable_row_id(
                'native-dispatch', task.tenant_id, task.id), missing_reason='native_result_retirement_dispatch_missing')
            if (task.status != 'completed' or journal.state != 'accepted'
                    or receipt.get('command_digest') != row.manifest['command_digest']
                    or receipt.get('authority', {}).get('assignment_revision') != journal.assignment_revision
                    or receipt.get('authority', {}).get('worker_id') != journal.worker_id
                    or receipt.get('authority', {}).get('worker_url') != journal.worker_url
                    or (journal.worker_id, journal.worker_url) != (
                        row.manifest['owner']['worker_id'], row.manifest['owner']['worker_url'])
                    or journal.binding_digest != native_dispatch_binding_digest(task,
                        worker_id=journal.worker_id, worker_url=journal.worker_url,
                        assignment_revision=journal.assignment_revision)
                    or (task.worker_execution_context or {}).get(MANIFEST_CONTEXT_FIELD) != row.manifest):
                raise ValueError('native_result_retirement_confirmed_receipt_required')
            result = native_task_result(task, command)
            output = result.output_data
            retired = output.get('retired_entries')
            manifest_entries = row.manifest['entries']
            if (not isinstance(retired, list) or len(retired) > len(manifest_entries)
                    or any(entry not in manifest_entries for entry in retired)
                    or len({native_context_digest(entry) for entry in retired}) != len(retired)
                    or output.get('retirement_manifest_digest') != row.manifest_digest
                    or type(output.get('compacted_results')) is not int
                    or not 0 <= output['compacted_results'] <= len(retired)
                    or output.get('replay_tombstones_preserved') is not True):
                raise ValueError('native_result_retirement_acknowledgement_denied')
            owner = row.manifest['owner']
            for entry in retired:
                key = retirement_marker_key(tenant_id=task.tenant_id, project_id=task.project_id,
                                           owner=owner, task_id=entry['task_id'])
                previous = session.get(NativeResultRetirementMarkerDB, key)
                if previous is not None:
                    if (previous.binding_digest != entry['binding_digest'] or previous.tenant_id != task.tenant_id
                            or previous.project_id != task.project_id or previous.task_id != entry['task_id']
                            or any(getattr(previous, field) != value for field, value in owner.items())):
                        raise ValueError('native_result_retirement_marker_binding_denied')
                    continue
                session.add(NativeResultRetirementMarkerDB(id=key, tenant_id=task.tenant_id,
                    project_id=task.project_id, **owner, task_id=entry['task_id'],
                    binding_digest=entry['binding_digest'], maintenance_task_id=task.id, confirmed_at=now))
            row.state, row.updated_at = 'confirmed', now
            session.add(row)
            return len(retired)

    def reconcile(self, *, tenant_id=None, project_id=None, limit=64):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('native_result_retirement_reconcile_limit_invalid')
        if (tenant_id is None) != (project_id is None):
            raise ValueError('native_result_retirement_reconcile_scope_invalid')
        ids = self._repository.pending_completed(tenant_id=tenant_id, project_id=project_id, limit=limit)
        confirmed = 0
        deadline = time.monotonic() + 5
        for task_id in ids:
            if time.monotonic() >= deadline:
                break
            try:
                self.confirm(task_id=task_id)
                confirmed += 1
            except (ValueError, RuntimeError):
                self._repository.block_acknowledgement(task_id)
        return confirmed

    @staticmethod
    def _task(session, task_id):
        return require_locked_row(session, TaskDB, task_id, missing_reason='native_result_retirement_task_missing')

    def _now(self):
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('native_result_retirement_clock_invalid')
        return now
