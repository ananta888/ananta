import os
import sys

root = os.path.abspath('.')
docs = [
    'docs/security_baseline.md',
    'docs/hub_fallback.md',
    'docs/execution_scope.md',
    'docs/artifacts_and_routing.md',
    'docs/frontend_goal_ux.md'
]
missing = [d for d in docs if not os.path.exists(os.path.join(root, d))]
if missing:
    print('Missing docs:', missing)
    sys.exit(1)
print('All docs present')
sys.exit(0)
