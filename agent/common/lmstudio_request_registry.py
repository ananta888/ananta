"""Registry for in-flight LM Studio HTTP sessions, keyed by goal_id/task_id."""
from __future__ import annotations

import logging
import threading
import weakref
from typing import Optional

_lock = threading.Lock()
_goal_sessions: dict[str, list[weakref.ref]] = {}
_task_sessions: dict[str, list[weakref.ref]] = {}
_cancelled_goals: set[str] = set()
_cancelled_tasks: set[str] = set()
_thread_context: dict[int, tuple[Optional[str], Optional[str]]] = {}

log = logging.getLogger(__name__)


def set_thread_context(goal_id: Optional[str], task_id: Optional[str]) -> None:
    tid = threading.current_thread().ident
    gid = str(goal_id or "").strip() or None
    tid2 = str(task_id or "").strip() or None
    with _lock:
        if gid:
            _cancelled_goals.discard(gid)
        if tid2:
            _cancelled_tasks.discard(tid2)
        if gid or tid2:
            _thread_context[tid] = (gid, tid2)
        else:
            _thread_context.pop(tid, None)


def clear_thread_context() -> None:
    tid = threading.current_thread().ident
    with _lock:
        _thread_context.pop(tid, None)


def _get_current_context() -> tuple[Optional[str], Optional[str]]:
    tid = threading.current_thread().ident
    with _lock:
        ctx = _thread_context.get(tid)
    if not ctx:
        return None, None
    return ctx


def create_and_register_session():
    import requests

    session = requests.Session()
    goal_id, task_id = _get_current_context()
    key = goal_id or task_id
    if goal_id or task_id:
        ref: weakref.ref = weakref.ref(session)
        with _lock:
            if goal_id:
                _goal_sessions.setdefault(goal_id, []).append(ref)
            if task_id:
                _task_sessions.setdefault(task_id, []).append(ref)
    return session, key


def register_existing_session(session) -> Optional[str]:
    goal_id, task_id = _get_current_context()
    key = goal_id or task_id
    if not (goal_id or task_id):
        return key
    ref: weakref.ref = weakref.ref(session)
    with _lock:
        if goal_id:
            _goal_sessions.setdefault(goal_id, []).append(ref)
        if task_id:
            _task_sessions.setdefault(task_id, []).append(ref)
    return key


def release_session(key: Optional[str], session) -> None:
    if not key:
        return
    with _lock:
        for session_map in (_goal_sessions, _task_sessions):
            for map_key in list(session_map.keys()):
                refs = session_map.get(map_key) or []
                session_map[map_key] = [r for r in refs if r() is not None and r() is not session]
                if not session_map[map_key]:
                    session_map.pop(map_key, None)


def cancel_goal(goal_id: str) -> int:
    key = str(goal_id or "").strip()
    if not key:
        return 0
    with _lock:
        _cancelled_goals.add(key)
        refs = _goal_sessions.pop(key, [])
    count = _close_refs(refs)
    if count:
        log.info("LMStudio registry: cancelled %d in-flight request(s) for goal=%s", count, key)
    return count


def cancel_task(task_id: str) -> int:
    key = str(task_id or "").strip()
    if not key:
        return 0
    with _lock:
        _cancelled_tasks.add(key)
        refs = _task_sessions.pop(key, [])
    count = _close_refs(refs)
    if count:
        log.info("LMStudio registry: cancelled %d in-flight request(s) for task=%s", count, key)
    return count


def cancel_all() -> int:
    with _lock:
        _cancelled_goals.update(_goal_sessions.keys())
        _cancelled_tasks.update(_task_sessions.keys())
        all_refs = [r for refs in _goal_sessions.values() for r in refs]
        _goal_sessions.clear()
        all_refs.extend([r for refs in _task_sessions.values() for r in refs])
        _task_sessions.clear()
    count = _close_refs(all_refs)
    if count:
        log.info("LMStudio registry: cancelled all %d in-flight request(s)", count)
    return count


def active_counts() -> dict[str, int]:
    with _lock:
        return {k: sum(1 for r in refs if r() is not None) for k, refs in _goal_sessions.items()}


def active_task_counts() -> dict[str, int]:
    with _lock:
        return {k: sum(1 for r in refs if r() is not None) for k, refs in _task_sessions.items()}


def is_cancelled(goal_id: Optional[str], task_id: Optional[str]) -> bool:
    gid = str(goal_id or "").strip()
    tid = str(task_id or "").strip()
    with _lock:
        return (bool(gid) and gid in _cancelled_goals) or (bool(tid) and tid in _cancelled_tasks)


def _close_refs(refs: list[weakref.ref]) -> int:
    count = 0
    for ref in refs:
        session = ref()
        if session is not None:
            try:
                session.close()
                count += 1
            except Exception:
                pass
    return count
