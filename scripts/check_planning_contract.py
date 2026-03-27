import os
import sys
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root not in sys.path:
    sys.path.insert(0, root)

from src.hub.planning import PlanningService


def main():
    ps = PlanningService()
    resp = ps.compat_adapter({'goal': 'Step one. Step two.'})
    assert 'plan_id' in resp and 'nodes' in resp, f'Invalid response: {resp}'
    for n in resp['nodes']:
        assert 'id' in n and 'title' in n and 'depends_on' in n
    print('Planning contract OK')


if __name__ == '__main__':
    main()
