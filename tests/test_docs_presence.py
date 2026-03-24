import os


def test_docs_exist():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    docs = [
        'docs/security_baseline.md',
        'docs/hub_fallback.md',
        'docs/execution_scope.md',
        'docs/artifacts_and_routing.md',
        'docs/frontend_goal_ux.md'
    ]
    missing = []
    for d in docs:
        if not os.path.exists(os.path.join(root, d)):
            missing.append(d)
    assert not missing, f"Missing docs: {missing}"
