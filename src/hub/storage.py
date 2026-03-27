import sqlite3
import json
from typing import Optional, List
from .models import Goal, Plan, PlanNode, Worker


class Storage:
    """Simple SQLite-backed storage for hub entities. Intended as a small, dependency-free prototype.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or ':memory:'
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_tables()

    def init_tables(self):
        cur = self.conn.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, summary TEXT, source TEXT)')
        cur.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, goal_id TEXT, title TEXT, rationale TEXT, trace_id TEXT)')
        cur.execute('CREATE TABLE IF NOT EXISTS plan_nodes (id TEXT PRIMARY KEY, plan_id TEXT, title TEXT, depends_on TEXT, rationale TEXT)')
        cur.execute('CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, goal_id TEXT, plan_node_id TEXT, task_id TEXT, artifact_type TEXT, content TEXT, metadata TEXT, trace_id TEXT)')
        cur.execute('CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, roles TEXT, capabilities TEXT)')
        self.conn.commit()

    def create_goal(self, goal: Goal):
        cur = self.conn.cursor()
        cur.execute('INSERT INTO goals (id, summary, source) VALUES (?, ?, ?)', (goal.id, goal.summary, goal.source))
        self.conn.commit()
        return goal.id

    def create_plan(self, plan: Plan, trace_id: Optional[str] = None):
        cur = self.conn.cursor()
        cur.execute('INSERT INTO plans (id, goal_id, title, rationale, trace_id) VALUES (?, ?, ?, ?, ?)', (plan.id, plan.goal_id, plan.title, plan.rationale, trace_id))
        self.conn.commit()
        return plan.id

    def add_plan_node(self, node: PlanNode):
        cur = self.conn.cursor()
        depends_json = json.dumps(node.depends_on or [])
        cur.execute('INSERT INTO plan_nodes (id, plan_id, title, depends_on, rationale) VALUES (?, ?, ?, ?, ?)', (node.id, node.plan_id, node.title, depends_json, node.rationale))
        self.conn.commit()
        return node.id

    def get_plan_nodes(self, plan_id: str) -> List[PlanNode]:
        cur = self.conn.cursor()
        cur.execute('SELECT * FROM plan_nodes WHERE plan_id = ?', (plan_id,))
        rows = cur.fetchall()
        nodes = []
        for r in rows:
            depends = json.loads(r['depends_on']) if r['depends_on'] else []
            node = PlanNode(id=r['id'], plan_id=r['plan_id'], title=r['title'], depends_on=depends, rationale=r['rationale'])
            nodes.append(node)
        return nodes

    def create_worker(self, worker: Worker):
        cur = self.conn.cursor()
        roles_json = json.dumps(worker.roles or [])
        caps_json = json.dumps(worker.capabilities or [])
        cur.execute('INSERT INTO workers (id, roles, capabilities) VALUES (?, ?, ?)', (worker.id, roles_json, caps_json))
        self.conn.commit()
        return worker.id

    def find_workers_for_capability(self, capability: str) -> List[Worker]:
        cur = self.conn.cursor()
        cur.execute('SELECT * FROM workers')
        rows = cur.fetchall()
        matches = []
        for r in rows:
            caps = json.loads(r['capabilities'] or '[]')
            roles = json.loads(r['roles'] or '[]')
            if capability in caps:
                matches.append(Worker(id=r['id'], roles=roles, capabilities=caps))
        return matches

    def add_artifact(self, goal_id: Optional[str], plan_node_id: Optional[str], task_id: Optional[str], artifact_type: str, content: str, metadata: Optional[str] = None, trace_id: Optional[str] = None) -> str:
        cur = self.conn.cursor()
        # use simple uuid-like id
        aid = f"artifact-{abs(hash((goal_id, plan_node_id, task_id, artifact_type, content))) % (10**12)}"
        cur.execute('INSERT INTO artifacts (id, goal_id, plan_node_id, task_id, artifact_type, content, metadata, trace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (aid, goal_id, plan_node_id, task_id, artifact_type, content, metadata, trace_id))
        self.conn.commit()
        return aid

    def get_artifacts(self, goal_id: Optional[str] = None, plan_node_id: Optional[str] = None) -> List[dict]:
        cur = self.conn.cursor()
        if goal_id:
            cur.execute('SELECT * FROM artifacts WHERE goal_id = ?', (goal_id,))
        elif plan_node_id:
            cur.execute('SELECT * FROM artifacts WHERE plan_node_id = ?', (plan_node_id,))
        else:
            cur.execute('SELECT * FROM artifacts')
        return [dict(r) for r in cur.fetchall()]

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
