from src.tasks import TaskStore


def test_task_store_add_and_next():
    store = TaskStore()
    store.add_task("t1", agent="a")
    store.add_task("t2", agent="b")
    nxt = store.next_task("a")
    assert nxt["task"] == "t1"
    assert store.list_tasks() == [{"task": "t2", "agent": "b", "template": None}]
