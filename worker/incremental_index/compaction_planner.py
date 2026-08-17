"""
CodeCompass Incremental Index - Compaction Planner

Plans optimal compaction strategies to reduce layer chain fragmentation.
"""
import json
import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime


@dataclass
class CompactionCandidate:
    """Represents a set of layers that should be compacted together."""
    layer_ids: List[str]
    total_size_bytes: int
    fragment_count: int
    estimated_savings_bytes: int
    priority_score: float  # Higher = more urgent


@dataclass
class CompactionPlan:
    """A complete plan for compaction operations."""
    plan_id: str
    created_at: str
    profile_name: str
    candidates: List[CompactionCandidate]
    total_estimated_savings_bytes: int
    strategy: str  # 'aggressive', 'balanced', 'conservative'
    
    def to_dict(self) -> dict:
        return {
            'plan_id': self.plan_id,
            'created_at': self.created_at,
            'profile_name': self.profile_name,
            'candidates': [
                {
                    'layer_ids': c.layer_ids,
                    'total_size_bytes': c.total_size_bytes,
                    'fragment_count': c.fragment_count,
                    'estimated_savings_bytes': c.estimated_savings_bytes,
                    'priority_score': c.priority_score
                }
                for c in self.candidates
            ],
            'total_estimated_savings_bytes': self.total_estimated_savings_bytes,
            'strategy': self.strategy
        }


class CompactionPlanner:
    """Analyzes layer chains and creates optimized compaction plans."""
    
    def __init__(self, layer_store_path: str):
        self.layer_store_path = Path(layer_store_path)
        
    def analyze_fragmentation(self, profile_name: str) -> Dict[str, any]:
        """Analyze fragmentation level for a profile's layer chain."""
        # Simulated analysis - in real impl would read from layer store
        head_ref = self.layer_store_path / "heads" / f"{profile_name}.json"
        
        if not head_ref.exists():
            return {'fragmentation_level': 0, 'layer_count': 0}
        
        # Mock data for demonstration
        return {
            'fragmentation_level': 0.65,  # 65% fragmented
            'layer_count': 12,
            'oldest_layer_age_days': 45,
            'redundant_artifacts': 234
        }
    
    def create_plan(
        self, 
        profile_name: str, 
        strategy: str = 'balanced'
    ) -> CompactionPlan:
        """Create a compaction plan for the given profile."""
        analysis = self.analyze_fragmentation(profile_name)
        
        if analysis['fragmentation_level'] < 0.2:
            # No compaction needed
            return CompactionPlan(
                plan_id=self._generate_plan_id(profile_name),
                created_at=datetime.utcnow().isoformat(),
                profile_name=profile_name,
                candidates=[],
                total_estimated_savings_bytes=0,
                strategy=strategy
            )
        
        # Create compaction candidates based on strategy
        candidates = self._identify_candidates(profile_name, strategy, analysis)
        
        total_savings = sum(c.estimated_savings_bytes for c in candidates)
        
        return CompactionPlan(
            plan_id=self._generate_plan_id(profile_name),
            created_at=datetime.utcnow().isoformat(),
            profile_name=profile_name,
            candidates=candidates,
            total_estimated_savings_bytes=total_savings,
            strategy=strategy
        )
    
    def _identify_candidates(
        self, 
        profile_name: str, 
        strategy: str,
        analysis: Dict
    ) -> List[CompactionCandidate]:
        """Identify layer groups that should be compacted."""
        candidates = []
        
        # Mock implementation - would analyze actual layer metadata
        if strategy == 'aggressive':
            # Compact frequently, smaller groups
            candidates.append(CompactionCandidate(
                layer_ids=['layer_001', 'layer_002', 'layer_003'],
                total_size_bytes=1500000,
                fragment_count=3,
                estimated_savings_bytes=450000,
                priority_score=0.85
            ))
        elif strategy == 'balanced':
            # Moderate compaction
            candidates.append(CompactionCandidate(
                layer_ids=['layer_001', 'layer_002', 'layer_003', 'layer_004'],
                total_size_bytes=2000000,
                fragment_count=4,
                estimated_savings_bytes=700000,
                priority_score=0.70
            ))
        else:  # conservative
            # Only compact when necessary, larger groups
            candidates.append(CompactionCandidate(
                layer_ids=['layer_001', 'layer_002', 'layer_003', 'layer_004', 'layer_005'],
                total_size_bytes=2500000,
                fragment_count=5,
                estimated_savings_bytes=1000000,
                priority_score=0.55
            ))
        
        return candidates
    
    def _generate_plan_id(self, profile_name: str) -> str:
        """Generate unique plan ID."""
        timestamp = datetime.utcnow().isoformat()
        content = f"{profile_name}:{timestamp}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def execute_plan(self, plan: CompactionPlan, dry_run: bool = True) -> Dict:
        """Execute a compaction plan (or simulate if dry_run)."""
        results = {
            'plan_id': plan.plan_id,
            'executed_at': datetime.utcnow().isoformat(),
            'dry_run': dry_run,
            'operations': []
        }
        
        for candidate in plan.candidates:
            op = {
                'operation': 'compact_layers',
                'source_layers': candidate.layer_ids,
                'estimated_savings': candidate.estimated_savings_bytes,
                'status': 'simulated' if dry_run else 'completed'
            }
            results['operations'].append(op)
        
        return results


if __name__ == '__main__':
    # Example usage
    planner = CompactionPlanner('/tmp/codecompass/layers')
    plan = planner.create_plan('default', 'balanced')
    print(json.dumps(plan.to_dict(), indent=2))
