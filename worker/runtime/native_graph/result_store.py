"""Worker-local durable result slots; no Hub database, queue or evidence issuance."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_result_binding import native_result_binding
from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_TASK_KIND,
    maintenance_task_owner,
    require_result_store_identifier,
)
from ananta_contracts.native_result_retirement import verify_retirement_manifest

MAX_RESULT_BYTES = 262144


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SQLiteNativeResultStore:
    def __init__(self, path, *, clock=time.time, retention_seconds=86400, max_records=10000,
                 expire_on_open=True, max_tombstones=1000000, max_maintenance_records=64,
                 store_binding_id=None):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.is_symlink():
            raise ValueError("native_result_store_private_path_required")
        parent = self.path.parent
        if not parent.is_dir() or parent.is_symlink() or parent.resolve() != parent:
            raise ValueError("native_result_store_private_directory_required")
        info = parent.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("native_result_store_private_directory_required")
        if type(max_records) is not int or not 1 <= max_records <= 100000:
            raise ValueError("native_result_store_limit_invalid")
        if type(max_tombstones) is not int or not 1 <= max_tombstones <= 10000000:
            raise ValueError('native_result_store_tombstone_limit_invalid')
        if type(max_maintenance_records) is not int or not 0 <= max_maintenance_records <= 1000:
            raise ValueError('native_result_store_maintenance_limit_invalid')
        if store_binding_id is not None:
            require_result_store_identifier(store_binding_id)
        if type(retention_seconds) is not int or not 1 <= retention_seconds <= 604800:
            raise ValueError("native_result_store_retention_invalid")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("native_result_store_private_file_required")
        finally:
            os.close(fd)
        self._clock, self._retention, self._max_records = clock, retention_seconds, max_records
        self._max_tombstones = max_tombstones
        self._max_maintenance_records, self._store_binding_id = max_maintenance_records, store_binding_id
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS native_result_store_identity (
                singleton INTEGER PRIMARY KEY CHECK (singleton=1), binding_id TEXT NOT NULL)''')
            identity = db.execute('SELECT binding_id FROM native_result_store_identity WHERE singleton=1').fetchone()
            if store_binding_id is not None:
                if identity is not None and identity['binding_id'] != store_binding_id:
                    raise ValueError('native_result_store_binding_changed')
                if identity is None:
                    db.execute('INSERT INTO native_result_store_identity VALUES (1, ?)', (store_binding_id,))
            db.execute("""CREATE TABLE IF NOT EXISTS native_results (
                tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, binding_digest TEXT NOT NULL,
                state TEXT NOT NULL, result_json TEXT, result_digest TEXT, expires_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, task_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS native_results_pending_expiry "
                       "ON native_results(expires_at) WHERE state != 'expired'")
            db.execute("""CREATE TABLE IF NOT EXISTS native_result_scopes (
                tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, project_id TEXT NOT NULL,
                binding_digest TEXT NOT NULL, PRIMARY KEY (tenant_id, task_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS native_result_tombstones (
                tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, binding_digest TEXT NOT NULL,
                permit_digest TEXT NOT NULL, PRIMARY KEY (tenant_id, task_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS native_result_maintenance_reservations (
                tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, binding_digest TEXT NOT NULL,
                PRIMARY KEY (tenant_id, task_id))""")
        if expire_on_open:
            self.expire_results()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _binding(task, evidence_assignment=None):
        return native_result_binding(task, evidence_assignment)

    def compact_results(self, manifest, *, task, owner, key_ring):
        return self.compact_results_receipt(manifest, task=task, owner=owner, key_ring=key_ring)['compacted_results']

    def compact_results_receipt(self, manifest, *, task, owner, key_ring):
        """Verify a Hub permit, then atomically trade expired slots for permanent denial.

        This frees logical result capacity, not a guarantee of physical erasure.
        Unexpired, unscoped and differently bound slots are never compacted.
        """
        now = self._clock()
        permit = verify_retirement_manifest(manifest, task=task, owner=owner, key_ring=key_ring, now=now)
        permit_digest = _digest(_encode(permit))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Lock acquisition can wait; never use a permit that expired while
            # another connection held SQLite's write lock.
            now = self._clock()
            verify_retirement_manifest(manifest, task=task, owner=owner, key_ring=key_ring, now=now)
            candidates = []
            retired = []
            for entry in permit['entries']:
                marker = db.execute('SELECT binding_digest FROM native_result_tombstones '
                                    'WHERE tenant_id=? AND task_id=?',
                                    (permit['tenant_id'], entry['task_id'])).fetchone()
                if marker is not None:
                    if marker['binding_digest'] == entry['binding_digest']:
                        retired.append(dict(entry))
                    continue
                row = db.execute('''SELECT r.task_id, r.binding_digest FROM native_results r
                    JOIN native_result_scopes s ON s.tenant_id=r.tenant_id AND s.task_id=r.task_id
                        AND s.binding_digest=r.binding_digest
                    WHERE r.tenant_id=? AND s.project_id=? AND r.task_id=? AND r.binding_digest=?
                        AND r.expires_at<=?''', (permit['tenant_id'], permit['project_id'],
                    entry['task_id'], entry['binding_digest'], now)).fetchone()
                if row is not None:
                    candidates.append(row)
                    retired.append(dict(entry))
            total = db.execute('SELECT count(*) FROM native_result_tombstones').fetchone()[0]
            if total + len(candidates) > self._max_tombstones:
                raise ValueError('native_result_store_tombstones_full')
            for row in candidates:
                db.execute('INSERT INTO native_result_tombstones VALUES (?, ?, ?, ?)',
                           (permit['tenant_id'], row['task_id'], row['binding_digest'], permit_digest))
                db.execute('DELETE FROM native_result_scopes WHERE tenant_id=? AND task_id=?',
                           (permit['tenant_id'], row['task_id']))
                db.execute('DELETE FROM native_result_maintenance_reservations WHERE tenant_id=? AND task_id=?',
                           (permit['tenant_id'], row['task_id']))
                db.execute('DELETE FROM native_results WHERE tenant_id=? AND task_id=?',
                           (permit['tenant_id'], row['task_id']))
            return {'compacted_results': len(candidates), 'retired_entries': retired,
                    'retirement_manifest_digest': native_context_digest(manifest)}

    def expire_results(self, *, batch_size=100, tenant_id=None, project_id=None):
        """Scrub one bounded batch without hydrating content or removing replay fences."""
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError("native_result_store_expiry_batch_invalid")
        scoped = tenant_id is not None or project_id is not None
        if scoped and any(not isinstance(value, str) or not 0 < len(value) <= 256
                          or any(ord(char) < 33 for char in value) for value in (tenant_id, project_id)):
            raise ValueError('native_result_store_expiry_scope_invalid')
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if scoped:
                changed = db.execute("""UPDATE native_results
                    SET state='expired', result_json=NULL, result_digest=NULL
                    WHERE rowid IN (SELECT r.rowid FROM native_results r JOIN native_result_scopes s
                        ON s.tenant_id=r.tenant_id AND s.task_id=r.task_id AND s.binding_digest=r.binding_digest
                        WHERE r.state != 'expired' AND r.expires_at<=? AND r.tenant_id=? AND s.project_id=?
                        ORDER BY r.expires_at, r.rowid LIMIT ?)""",
                    (self._clock(), tenant_id, project_id, batch_size))
                return changed.rowcount
            changed = db.execute("""UPDATE native_results
                SET state='expired', result_json=NULL, result_digest=NULL
                WHERE rowid IN (SELECT rowid FROM native_results
                    WHERE state != 'expired' AND expires_at<=?
                    ORDER BY expires_at, rowid LIMIT ?)""", (self._clock(), batch_size))
            return changed.rowcount

    def reserve(self, task, *, evidence_assignment=None, expire_before=True):
        projected, binding = self._binding(task, evidence_assignment)
        self._expire_before(expire_before)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute('SELECT 1 FROM native_result_tombstones WHERE tenant_id=? AND task_id=?',
                          (projected['tenant_id'], projected['id'])).fetchone() is not None:
                raise ValueError('native_result_execution_already_reserved')
            ordinary_count = db.execute('''SELECT count(*) FROM native_results r
                LEFT JOIN native_result_maintenance_reservations m ON m.tenant_id=r.tenant_id
                    AND m.task_id=r.task_id AND m.binding_digest=r.binding_digest
                WHERE m.task_id IS NULL''').fetchone()[0]
            emergency = ordinary_count >= self._max_records
            if emergency:
                if not self._maintenance_reservation_allowed(projected):
                    raise ValueError('native_result_store_full')
                if db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] >= (
                        self._max_maintenance_records):
                    raise ValueError('native_result_store_maintenance_full')
            try:
                db.execute("INSERT INTO native_results VALUES (?, ?, ?, 'executing', NULL, NULL, ?)",
                           (projected["tenant_id"], projected["id"], binding, self._clock() + self._retention))
            except sqlite3.IntegrityError as exc:
                raise ValueError("native_result_execution_already_reserved") from exc
            if emergency:
                db.execute('INSERT INTO native_result_maintenance_reservations VALUES (?, ?, ?)',
                           (projected['tenant_id'], projected['id'], binding))
            project = projected.get('project_id')
            if isinstance(project, str) and project:
                db.execute('INSERT INTO native_result_scopes VALUES (?, ?, ?, ?)',
                    (projected['tenant_id'], projected['id'], project, binding))

    def _maintenance_reservation_allowed(self, task):
        """Capacity classification only; execution still requires live Hub authority.

        The HTTP admission verifies the closed Task signature and recipient; the
        store resolver binds it to the installed registration before reservation.
        Legacy/unbound stores receive no additional capacity implicitly.
        """
        if task['task_kind'] != MAINTENANCE_TASK_KIND or self._store_binding_id is None:
            return False
        owner = maintenance_task_owner(task)
        return (owner is not None and owner['store_binding_id'] == self._store_binding_id
                and isinstance(task.get('project_id'), str) and bool(task['project_id']))

    def publish(self, task, result, *, evidence_assignment=None, expire_before=True):
        projected, binding = self._binding(task, evidence_assignment)
        if not isinstance(result, dict) or result.get("status") not in {"completed", "failed", "cancelled"}:
            raise ValueError("native_result_store_outcome_invalid")
        encoded = _encode(result)
        if len(encoded.encode()) > MAX_RESULT_BYTES:
            raise ValueError("native_result_store_outcome_limit")
        self._expire_before(expire_before)
        with self._connect() as db:
            changed = db.execute("""UPDATE native_results SET state='ready', result_json=?, result_digest=?
                WHERE tenant_id=? AND task_id=? AND binding_digest=? AND state='executing' AND expires_at>?""",
                                 (encoded, _digest(encoded), projected["tenant_id"], projected["id"],
                                  binding, self._clock()))
            if changed.rowcount != 1:
                raise ValueError("native_result_store_publication_denied")

    def read(self, task, *, evidence_assignment=None, expire_before=True):
        projected, binding = self._binding(task, evidence_assignment)
        self._expire_before(expire_before)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM native_results WHERE tenant_id=? AND task_id=?",
                             (projected["tenant_id"], projected["id"])).fetchone()
            if row is None or row["binding_digest"] != binding:
                raise ValueError("native_result_store_binding_denied")
            if row["expires_at"] <= self._clock():
                # Erase payload but retain the no-reexecution tombstone.
                db.execute("UPDATE native_results SET state='expired', result_json=NULL, result_digest=NULL "
                           "WHERE tenant_id=? AND task_id=?", (projected["tenant_id"], projected["id"]))
                expired = True
            else:
                expired = False
        if expired:
            raise ValueError("native_result_store_expired")
        if row["state"] != "ready":
            raise ValueError("native_result_store_not_ready")
        encoded = row["result_json"]
        if (not isinstance(encoded, str) or len(encoded.encode()) > MAX_RESULT_BYTES
                or _digest(encoded) != row["result_digest"]):
            raise ValueError("native_result_store_corrupt")
        result = json.loads(encoded)
        if _encode(result) != encoded:
            raise ValueError("native_result_store_corrupt")
        return result

    def _expire_before(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('native_result_store_expiry_option_invalid')
        if enabled:
            self.expire_results()
