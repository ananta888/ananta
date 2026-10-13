"""One finite Hub maintenance tick; no Worker timer or independent task loop."""

import time
from typing import Protocol

from agent.services.native_result_maintenance_policy import configured_maintenance_schedules
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError


class MaintenanceCadenceStorePort(Protocol):
    def pending(self, *, limit=64): ...
    def reserve(self, schedule, *, now): ...
    def finish(self, reservation, *, status, reason_code='', now): ...


class MaintenanceRunPort(Protocol):
    def advance(self, reservation): ...
    def cancel(self, reservation): ...


class MaintenanceAdmissionPort(Protocol):
    def require(self, schedule): ...


class NativeResultMaintenanceCadence:
    def __init__(self, *, store: MaintenanceCadenceStorePort, runs: MaintenanceRunPort,
                 admission: MaintenanceAdmissionPort, configuration, clock=time.time):
        self._store, self._runs, self._admission = store, runs, admission
        self._configuration, self._clock = configuration, clock

    def run_once(self):
        counts = {'reserved': 0, 'active': 0, 'completed': 0, 'cancelled': 0, 'failed': 0, 'blocked': 0, 'pending': 0}
        try:
            schedules = configured_maintenance_schedules(self._configuration())
        except Exception:
            schedules = ()
            counts['blocked'] = 1
        try:
            pending = self._store.pending()
        except Exception:
            return {**counts, 'blocked': 1, 'reason_code': 'native_maintenance_journal_unavailable'}
        counts['pending'] = len(pending)
        configured = {(item.tenant_id, item.project_id, item.schedule_id): item for item in schedules}
        handled = set()
        for reservation in pending:
            key = self._key(reservation.schedule)
            handled.add(key)
            current = configured.get(key)
            if current is None or current.digest != reservation.schedule.digest:
                self._cancel(reservation, counts)
            else:
                self._advance(reservation, counts)
        for schedule in schedules:
            if self._key(schedule) in handled:
                continue
            if counts['pending'] >= 64:
                counts['blocked'] += 1
                continue
            try:
                self._admission.require(schedule)
                reservation = self._store.reserve(schedule, now=self._clock())
                if reservation is not None:
                    counts['reserved'] += 1
                    counts['pending'] += 1
                    self._advance(reservation, counts)
            except Exception:
                counts['blocked'] += 1
        return counts

    @staticmethod
    def _key(schedule):
        return schedule.tenant_id, schedule.project_id, schedule.schedule_id

    def _cancel(self, reservation, counts):
        try:
            outcome = self._runs.cancel(reservation) or 'cancelled'
            self._store.finish(reservation, status=outcome, now=self._clock(),
                               reason_code='native_maintenance_policy_withdrawn')
            counts[outcome] += 1
            counts['pending'] -= 1
        except Exception:
            counts['blocked'] += 1  # Same reserved Run remains available for bounded reconciliation.

    def _advance(self, reservation, counts):
        try:
            self._admission.require(reservation.schedule)
        except Exception:
            self._cancel(reservation, counts)
            counts['blocked'] += 1
            return
        try:
            result = self._runs.advance(reservation)
            if result.status in {'completed', 'failed', 'cancelled'}:
                self._store.finish(reservation, status=result.status, now=self._clock())
                counts[result.status] += 1
                counts['pending'] -= 1
            elif result.status == 'running':
                counts['active'] += 1
            else:
                counts['blocked'] += 1
        except OptimisticConcurrencyError as exc:
            counts['active' if str(exc) == 'bpmn_control_lease_held' else 'blocked'] += 1
        except Exception:
            counts['blocked'] += 1  # Never invent completion or reserve a replacement after uncertainty.
