import os
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root not in sys.path:
    sys.path.insert(0, root)

from src.hub.planning import PlanningService
from src.hub.storage import Storage


def main():
    s = Storage('data\hub_demo.db')
    ps = PlanningService(s)
    plan = ps.plan_from_goal('Demo: create a small plan with steps for a sample goal')
    nodes = s.get_plan_nodes(plan.id)
    print('Plan', plan.id, 'created with', len(nodes), 'nodes')

if __name__ == '__main__':
    main()
