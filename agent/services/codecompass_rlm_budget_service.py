"""
CodeCompass RLM Budget & Abbruchregeln

Integriert Budget-Tracking und Abbruchlogik für rekursive Query-Plans in den bestehenden
Context Planner. Stellt sicher, dass globale und lokale Budgets deterministisch verwaltet
werden und harte Stop-Conditions eingehalten werden.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List
import hashlib


class StopReason(Enum):
    """Maschinenlesbare Gründe für das Stoppen eines rekursiven Plans"""
    MAX_DEPTH_REACHED = "max_depth_reached"
    MAX_NODES_EXCEEDED = "max_nodes_exceeded"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    TIME_BUDGET_EXCEEDED = "time_budget_exceeded"
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"
    REPEATED_QUERY_DETECTED = "repeated_query_detected"
    NO_NEW_EVIDENCE = "no_new_evidence"
    PARENT_STOPPED = "parent_stopped"
    ERROR = "error"
    MANUAL_ABORT = "manual_abort"


@dataclass
class BudgetState:
    """Aktueller Budget-Zustand für einen Knoten"""
    token_budget: int
    remaining_tokens: int
    time_budget_seconds: Optional[int] = None
    cost_budget_cents: Optional[float] = None
    max_nodes: int = 100
    max_depth: int = 5
    max_fan_out: int = 10
    nodes_created: int = 0
    start_time: datetime = field(default_factory=datetime.utcnow)
    
    def can_create_child(self) -> bool:
        """Prüft ob ein weiterer Child-Knoten erstellt werden darf"""
        if self.nodes_created >= self.max_fan_out:
            return False
        if self.remaining_tokens < 100:  # Minimum pro Node
            return False
        return True
    
    def allocate_for_child(self, estimated_tokens: int) -> Optional['BudgetState']:
        """Reserviert Budget für einen Child-Knoten"""
        if not self.can_create_child():
            return None
        
        child_budget = min(estimated_tokens, self.remaining_tokens // 2)
        child_time = None
        if self.time_budget_seconds:
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            remaining_time = max(0, self.time_budget_seconds - elapsed)
            child_time = int(remaining_time / 2)
        
        return BudgetState(
            token_budget=child_budget,
            remaining_tokens=child_budget,
            time_budget_seconds=child_time,
            cost_budget_cents=self.cost_budget_cents,
            max_nodes=self.max_nodes - self.nodes_created,
            max_depth=self.max_depth - 1,
            max_fan_out=min(self.max_fan_out, self.nodes_created + 3),
            start_time=datetime.utcnow()
        )
    
    def consume_tokens(self, tokens: int) -> None:
        """Verbraucht Token aus dem Budget"""
        self.remaining_tokens = max(0, self.remaining_tokens - tokens)
        self.nodes_created += 1
    
    def is_exhausted(self) -> bool:
        """Prüft ob das Budget aufgebraucht ist"""
        if self.remaining_tokens < 50:
            return True
        if self.time_budget_seconds:
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            if elapsed > self.time_budget_seconds:
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialisiert Budget-State für JSON"""
        return {
            "token_budget": self.token_budget,
            "remaining_tokens": self.remaining_tokens,
            "time_budget_seconds": self.time_budget_seconds,
            "cost_budget_cents": self.cost_budget_cents,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "max_fan_out": self.max_fan_out,
            "nodes_created": self.nodes_created,
            "start_time": self.start_time.isoformat()
        }


@dataclass
class QueryFingerprint:
    """Normalisierter Query-Fingerprint zur Zykluserkennung"""
    fingerprint: str
    query: str
    intent: str
    scope_hash: str
    
    @classmethod
    def create(cls, query: str, intent: str, source_refs: List[str]) -> 'QueryFingerprint':
        """Erstellt einen normalisierten Fingerprint"""
        normalized_query = ' '.join(query.lower().split())
        scope_hash = hashlib.sha256(''.join(sorted(source_refs)).encode()).hexdigest()[:16]
        fp_content = f"{normalized_query}|{intent}|{scope_hash}"
        fingerprint = hashlib.sha256(fp_content.encode()).hexdigest()[:32]
        
        return cls(
            fingerprint=fingerprint,
            query=normalized_query,
            intent=intent,
            scope_hash=scope_hash
        )


class RLMBudgetManager:
    """
    Verwaltet Budgets und Abbruchregeln für rekursive Query-Plans.
    
    Features:
    - Globale und lokale Budgetverteilung
    - Harte Stop-Conditions (Depth, Nodes, Tokens, Time, Cost)
    - Query-Fingerprinting zur Zykluserkennung
    - Deterministische Budgetberechnung
    """
    
    def __init__(
        self,
        global_token_budget: int = 100000,
        global_time_budget_seconds: int = 600,
        max_depth: int = 5,
        max_fan_out: int = 10,
        max_total_nodes: int = 200
    ):
        self.global_token_budget = global_token_budget
        self.global_time_budget_seconds = global_time_budget_seconds
        self.max_depth = max_depth
        self.max_fan_out = max_fan_out
        self.max_total_nodes = max_total_nodes
        
        self.budget_states: Dict[str, BudgetState] = {}
        self.query_fingerprints: set = set()
        self.total_nodes_created = 0
        self.start_time = datetime.utcnow()
    
    def create_root_budget(self, 
                          token_budget: Optional[int] = None,
                          time_budget: Optional[int] = None) -> BudgetState:
        """Erstellt initiales Budget für Root-Knoten"""
        budget = BudgetState(
            token_budget=token_budget or self.global_token_budget,
            remaining_tokens=token_budget or self.global_token_budget,
            time_budget_seconds=time_budget or self.global_time_budget_seconds,
            max_depth=self.max_depth,
            max_fan_out=self.max_fan_out,
            max_nodes=self.max_total_nodes,
            start_time=self.start_time
        )
        self.budget_states["root"] = budget
        return budget
    
    def register_query(self, node_id: str, query: str, intent: str, 
                      source_refs: List[str]) -> Optional[StopReason]:
        """
        Registriert eine Query und prüft auf Duplikate/Zyklen.
        
        Returns:
            StopReason wenn Query abgelehnt wird, sonst None
        """
        fp = QueryFingerprint.create(query, intent, source_refs)
        
        if fp.fingerprint in self.query_fingerprints:
            return StopReason.REPEATED_QUERY_DETECTED
        
        self.query_fingerprints.add(fp.fingerprint)
        return None
    
    def check_stop_conditions(
        self,
        node_id: str,
        depth: int,
        budget_state: BudgetState,
        has_new_evidence: bool = True
    ) -> Optional[StopReason]:
        """
        Prüft alle Stop-Conditions für einen Knoten.
        
        Returns:
            StopReason wenn gestoppt werden muss, sonst None
        """
        if depth > self.max_depth:
            return StopReason.MAX_DEPTH_REACHED
        
        if self.total_nodes_created >= self.max_total_nodes:
            return StopReason.MAX_NODES_EXCEEDED
        
        if budget_state.is_exhausted():
            return StopReason.TOKEN_BUDGET_EXHAUSTED
        
        elapsed = (datetime.utcnow() - self.start_time).total_seconds()
        if budget_state.time_budget_seconds and elapsed > budget_state.time_budget_seconds:
            return StopReason.TIME_BUDGET_EXCEEDED
        
        if not has_new_evidence and depth > 0:
            return StopReason.NO_NEW_EVIDENCE
        
        return None
    
    def allocate_child_budget(
        self,
        parent_id: str,
        child_id: str,
        estimated_tokens: int
    ) -> Optional[BudgetState]:
        """
        Reserviert Budget für einen Child-Knoten.
        
        Returns:
            BudgetState für Child oder None wenn nicht möglich
        """
        if parent_id not in self.budget_states:
            return None
        
        parent_budget = self.budget_states[parent_id]
        
        if not parent_budget.can_create_child():
            return None
        
        child_budget = parent_budget.allocate_for_child(estimated_tokens)
        if child_budget:
            self.budget_states[child_id] = child_budget
            parent_budget.consume_tokens(estimated_tokens)
            self.total_nodes_created += 1
        
        return child_budget
    
    def get_remaining_global_budget(self) -> Dict[str, Any]:
        """Gibt verbleibendes globales Budget zurück"""
        elapsed = (datetime.utcnow() - self.start_time).total_seconds()
        remaining_time = max(0, self.global_time_budget_seconds - elapsed)
        
        total_remaining_tokens = sum(
            bs.remaining_tokens for bs in self.budget_states.values()
        )
        
        return {
            "remaining_time_seconds": remaining_time,
            "remaining_tokens": total_remaining_tokens,
            "total_nodes_created": self.total_nodes_created,
            "unique_queries_registered": len(self.query_fingerprints),
            "active_budget_states": len(self.budget_states)
        }


def integrate_with_context_planner(planner_service, rlm_enabled: bool = True):
    """
    Integriert RLM-Budget-Manager in bestehenden Context Planner.
    
    Diese Funktion erweitert den CodeCompassContextPlannerService um RLM-Fähigkeiten.
    """
    if not rlm_enabled:
        return None
    
    budget_manager = RLMBudgetManager(
        global_token_budget=getattr(planner_service, 'context_token_budget', 100000),
        global_time_budget_seconds=600,
        max_depth=5,
        max_fan_out=10,
        max_total_nodes=200
    )
    
    return budget_manager


if __name__ == "__main__":
    # Demo/Test
    manager = RLMBudgetManager(
        global_token_budget=10000,
        global_time_budget_seconds=300,
        max_depth=3,
        max_fan_out=5
    )
    
    root_budget = manager.create_root_budget()
    print(f"Root Budget: {root_budget.to_dict()}")
    
    # Test Query Registration
    stop = manager.register_query("node1", "How does auth work?", "architecture_overview", ["src/auth"])
    print(f"Query registered, stop reason: {stop}")
    
    # Test duplicate detection
    stop = manager.register_query("node2", "How does auth work?", "architecture_overview", ["src/auth"])
    print(f"Duplicate query stop reason: {stop}")
    
    # Test child allocation
    child_budget = manager.allocate_child_budget("root", "child1", 1000)
    if child_budget:
        print(f"Child budget created: {child_budget.to_dict()}")
    
    # Check stop conditions
    stop_reason = manager.check_stop_conditions("child1", depth=1, budget_state=child_budget)
    print(f"Stop condition check: {stop_reason}")
    
    print(f"Global budget remaining: {manager.get_remaining_global_budget()}")
