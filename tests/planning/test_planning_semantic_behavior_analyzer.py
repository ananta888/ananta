from agent.services.planning_semantic_behavior_analyzer import analyze_semantic_behavior


def test_detects_architecture_only_no_execution():
    assert "architecture_only_no_execution" in analyze_semantic_behavior(subtasks=[])


def test_detects_sequentializes_everything():
    subtasks = [
        {"title": "A", "description": "Do A", "task_kind": "coding", "depends_on": []},
        {"title": "B", "description": "Do B", "task_kind": "coding", "depends_on": ["1"]},
        {"title": "C", "description": "Do C", "task_kind": "coding", "depends_on": ["2"]},
    ]
    codes = analyze_semantic_behavior(subtasks=subtasks, parallel_default=True)
    assert "sequentializes_everything" in codes
