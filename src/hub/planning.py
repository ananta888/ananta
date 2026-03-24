from typing import Optional, Dict
from .storage import Storage
from .models import Goal, Plan, PlanNode


class PlanningService:
    """Small PlanningService prototype that materializes a Plan and PlanNodes from a short goal string.

    The real product implementation will be more sophisticated; this is a dependency-free prototype used
    for tests and compatibility adapters.
    """

    def __init__(self, storage: Optional[Storage] = None):
        self.storage = storage or Storage(':memory:')

    def plan_from_goal(self, summary: str, source: Optional[str] = None) -> Plan:
        goal = Goal(summary=summary, source=source)
        self.storage.create_goal(goal)
        plan = Plan(goal_id=goal.id, title=(summary or '')[:120])
        self.storage.create_plan(plan)
        sentences = [s.strip() for s in (summary or '').split('.') if s.strip()]
        prev_node = None
        for s in sentences:
            node = PlanNode(plan_id=plan.id, title=s)
            if prev_node is not None:
                node.depends_on = [prev_node.id]
            self.storage.add_plan_node(node)
            prev_node = node
        return plan

    def compat_adapter(self, legacy_payload: Dict) -> Dict:
        """Map a legacy auto-planner request shape to the new PlanningService and return a compatibility-safe response.

        Example legacy payloads might contain keys like 'goal', 'prompt' or 'text'.
        """
        goal_text = legacy_payload.get('goal') or legacy_payload.get('text') or legacy_payload.get('prompt')
        if not goal_text:
            raise ValueError('No goal text found in legacy payload')
        plan = self.plan_from_goal(goal_text)
        nodes = self.storage.get_plan_nodes(plan.id)
        return {"plan_id": plan.id, "nodes": [{"id": n.id, "title": n.title, "depends_on": n.depends_on} for n in nodes]}
