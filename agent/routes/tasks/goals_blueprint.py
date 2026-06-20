import threading

from flask import Blueprint

goals_bp = Blueprint("tasks_goals", __name__)

_PLANNING_SLOTS_LOCK = threading.Lock()
_PLANNING_SLOTS: threading.Semaphore | None = None
_PLANNING_SLOTS_CAPACITY: int = 0
