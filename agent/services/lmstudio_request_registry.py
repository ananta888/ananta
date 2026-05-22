"""Registry for in-flight LM Studio HTTP sessions, keyed by goal_id.

Calling session.close() on a requests.Session that is blocked inside a
session.post() raises ConnectionError in the calling thread, which is caught
in _post_lmstudio and converted to None — effectively aborting the request
without waiting for the 700s timeout to expire.

Usage:
  - set_thread_context(goal_id, task_id) at the start of a task dispatch thread
  - clear_thread_context()               in the finally block of the same thread
  - create_and_register_session()        in _post_lmstudio to get a tracked session
  - release_session(key, session)        after the request completes normally
  - cancel_goal(goal_id)                 to abort all in-flight requests for a goal
  - cancel_all()                         to abort everything (e.g. on restart)
"""
from __future__ import annotations

import logging
import threading
import weakref
from typing import Optional

_lock = threading.Lock()

# goal_id -> list of weakref(session)
_goal_sessions: dict[str, list[weakref.ref]] = {}
# task_id -> list of weakref(session)
_task_sessions: dict[str, list[weakref.ref]] = {}

# thread_ident → (goal_id, task_id)
_thread_context: dict[int, tuple[Optional[str], Optional[str]]] = {}

log = logging.getLogger(__name__)


def set_thread_context(goal_id: Optional[str], task_id: Optional[str]) -> None:
    """Call at the start of a task execution thread before any LM Studio calls."""
    tid = threading.current_thread().ident
    gid = str(goal_id or "").strip() or None
    tid2 = str(task_id or "").strip() or None
    with _lock:
        if gid or tid2:
            _thread_context[tid] = (gid, tid2)
        else:
            _thread_context.pop(tid, None)


def clear_thread_context() -> None:
    """Call in the finally block of a task execution thread."""
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
    """Create a fresh requests.Session and register it under the current thread's goal.

    Returns (session, key) where key is goal_id or task_id fallback, and may be None.
    """
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
    """Register an existing requests.Session under the current thread context."""
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
    """Remove a completed session from the registry."""
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
    """Close all in-flight LM Studio sessions for a goal. Returns number closed."""
    key = str(goal_id or "").strip()
    if not key:
        return 0
    with _lock:
        refs = _goal_sessions.pop(key, [])
    count = _close_refs(refs)
    if count:
        log.info("LMStudio registry: cancelled %d in-flight request(s) for goal=%s", count, key)
    return count


def cancel_task(task_id: str) -> int:
    """Close all in-flight LM Studio sessions for a task. Returns number closed."""
    key = str(task_id or "").strip()
    if not key:
        return 0
    with _lock:
        refs = _task_sessions.pop(key, [])
    count = _close_refs(refs)
    if count:
        log.info("LMStudio registry: cancelled %d in-flight request(s) for task=%s", count, key)
    return count


def cancel_all() -> int:
    """Close all tracked in-flight sessions. Returns total count closed."""
    with _lock:
        all_refs = [r for refs in _goal_sessions.values() for r in refs]
        _goal_sessions.clear()
        all_refs.extend([r for refs in _task_sessions.values() for r in refs])
        _task_sessions.clear()
    count = _close_refs(all_refs)
    if count:
        log.info("LMStudio registry: cancelled all %d in-flight request(s)", count)
    return count


def active_counts() -> dict[str, int]:
    """Returns {goal_id: session_count} for currently tracked sessions."""
    with _lock:
        return {k: sum(1 for r in refs if r() is not None) for k, refs in _goal_sessions.items()}


def active_task_counts() -> dict[str, int]:
    """Returns {task_id: session_count} for currently tracked sessions."""
    with _lock:
        return {k: sum(1 for r in refs if r() is not None) for k, refs in _task_sessions.items()}


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
