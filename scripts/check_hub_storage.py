import os
import sys
import json

# Make repo root importable so `src` is a package root
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root not in sys.path:
    sys.path.insert(0, root)

from src.hub.storage import Storage
from src.hub.planning import PlanningService
from src.hub.models import Worker


def main():
    s = Storage(':memory:')
    ps = PlanningService(s)
    plan = ps.plan_from_goal('Create a simple planner that splits goals into steps and records nodes.')
    nodes = s.get_plan_nodes(plan.id)
    print('Plan created:', plan.id)
    for n in nodes:
        print('-', n.id, n.title, 'depends_on:', n.depends_on)

    # Add artifact
    aid = s.add_artifact(goal_id=plan.goal_id, plan_node_id=(nodes[0].id if nodes else None), task_id='t1', artifact_type='report', content=json.dumps({'summary':'ok'}), metadata=json.dumps({'source':'check'}))
    print('Added artifact:', aid)

    # Register worker and query
    w = Worker(roles=['coder'], capabilities=['planning', 'python'])
    s.create_worker(w)
    matches = s.find_workers_for_capability('planning')
    print('Workers with capability planning:', [m.id for m in matches])
    assert len(matches) > 0


if __name__ == '__main__':
    main()
