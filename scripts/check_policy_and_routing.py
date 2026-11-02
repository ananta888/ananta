import os
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root not in sys.path:
    sys.path.insert(0, root)

from hub.models import Worker
from hub.policy import check_execution_allowed
from hub.security import has_capability
from hub.storage import Storage


def main():
    s = Storage(":memory:")
    w = Worker(roles=["coder"], capabilities=["python"])
    try:
        check_execution_allowed(w, ["planning"])
        raise SystemExit("Policy allowed missing capability")
    except PermissionError:
        print("Policy correctly blocked missing capability")

    w2 = Worker(roles=["planner"], capabilities=["planning"])
    s.create_worker(w2)
    matches = s.find_workers_for_capability("planning")
    assert any(m.id == w2.id for m in matches), "Worker routing failed"
    assert has_capability(w2, "planning")
    print("Policy and routing checks OK")


if __name__ == "__main__":
    main()
