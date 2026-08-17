"""
CodeCompass RLM - Recursive Query Planner

Creates multi-stage retrieval plans based on RLM context graph.
"""
import json
import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime


@dataclass
class RetrievalStep:
    """A single step in the recursive retrieval plan."""
    step_id: str
    node_id: str
    channels: List[str]  # ['vector', 'graph', 'fulltext']
    budget_tokens: int
    depth: int
    dependencies: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            'step_id': self.step_id,
            'node_id': self.node_id,
            'channels': self.channels,
            'budget_tokens': self.budget_tokens,
            'depth': self.depth,
            'dependencies': self.dependencies
        }


@dataclass
class RecursivePlan:
    """Complete recursive retrieval plan."""
    plan_id: str
    created_at: str
    root_node_id: str
    steps: List[RetrievalStep]
    total_budget_tokens: int
    max_depth: int
    
    def to_dict(self) -> dict:
        return {
            'plan_id': self.plan_id,
            'created_at': self.created_at,
            'root_node_id': self.root_node_id,
            'steps': [s.to_dict() for s in self.steps],
            'total_budget_tokens': self.total_budget_tokens,
            'max_depth': self.max_depth
        }


class RecursiveQueryPlanner:
    """Plans recursive retrieval queries based on RLM graph."""
    
    def __init__(self, max_depth: int = 5, default_budget: int = 2000):
        self.max_depth = max_depth
        self.default_budget = default_budget
        
    def create_plan(
        self, 
        root_node_id: str,
        rlm_graph: Dict,
        budget_override: Optional[int] = None
    ) -> RecursivePlan:
        """Create a recursive retrieval plan."""
        steps = []
        budget = budget_override or self.default_budget
        
        # BFS traversal to create steps
        visited = set()
        queue = [(root_node_id, 0)]  # (node_id, depth)
        
        while queue and len(steps) < 20:  # Limit steps
            node_id, depth = queue.pop(0)
            
            if node_id in visited or depth > self.max_depth:
                continue
                
            visited.add(node_id)
            
            # Create retrieval step
            step = self._create_step(node_id, depth, budget, visited)
            steps.append(step)
            
            # Add children to queue
            children = rlm_graph.get(node_id, {}).get('children', [])
            for child_id in children[:3]:  # Limit branching
                if child_id not in visited:
                    queue.append((child_id, depth + 1))
        
        total_budget = sum(s.budget_tokens for s in steps)
        
        return RecursivePlan(
            plan_id=self._generate_plan_id(root_node_id),
            created_at=datetime.utcnow().isoformat(),
            root_node_id=root_node_id,
            steps=steps,
            total_budget_tokens=total_budget,
            max_depth=max(d.depth for d in steps) if steps else 0
        )
    
    def _create_step(
        self, 
        node_id: str, 
        depth: int, 
        budget: int,
        visited: set
    ) -> RetrievalStep:
        """Create a single retrieval step."""
        # Adjust budget based on depth (deeper = less budget)
        depth_factor = max(0.3, 1.0 - (depth * 0.15))
        step_budget = int(budget * depth_factor)
        
        # Determine channels based on node type
        channels = ['vector']
        if depth == 0:
            channels.extend(['graph', 'fulltext'])
        elif depth <= 2:
            channels.append('graph')
        
        # Dependencies are previous steps at shallower depth
        deps = [s.step_id for s in [] if s.depth < depth]
        
        return RetrievalStep(
            step_id=f"step_{node_id[:8]}_{depth}",
            node_id=node_id,
            channels=channels,
            budget_tokens=step_budget,
            depth=depth,
            dependencies=deps
        )
    
    def _generate_plan_id(self, root_node_id: str) -> str:
        """Generate unique plan ID."""
        content = f"{root_node_id}:{datetime.utcnow().isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


if __name__ == '__main__':
    # Example usage
    planner = RecursiveQueryPlanner()
    
    mock_graph = {
        'node_001': {'children': ['node_002', 'node_003']},
        'node_002': {'children': ['node_004', 'node_005']},
        'node_003': {'children': ['node_006']}
    }
    
    plan = planner.create_plan('node_001', mock_graph)
    print(json.dumps(plan.to_dict(), indent=2))
