"""
CodeCompass Architecture Budget Policy (HAC-003)

Token-, Node-, Edge- und Depth-Budget-Policy für hierarchische Architekturkontexte.
Priorisiert relevante Verbindungen bei Budgetüberschreitung und erzeugt
expandierbare Trunkierung statt stiller Abschneidung.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class ArchitectureBudgetPolicy:
    """
    Budget-Policy für hierarchische Architekturkontexte.
    
    Definiert separate Budgets für Systemübersicht, Subsysteme, Komponenten
    und feingranulare Evidence mit harten Maxima und relativer Verteilung.
    """
    
    # Harte Maxima
    token_budget: int = 4000
    node_budget: int = 100
    edge_budget: int = 200
    depth_budget: int = 5  # 1=System, 5=Symbol
    
    # Relative Verteilung unter Gesamtbudget (Anteile 0.0-1.0)
    system_token_share: float = 0.15
    subsystem_token_share: float = 0.25
    component_token_share: float = 0.30
    file_token_share: float = 0.20
    symbol_token_share: float = 0.10
    
    # Priorisierung bei Trunkierung
    prefer_higher_levels: bool = True  # True = System/Subsystem bevorzugen
    preserve_connectivity: bool = True  # Zusammenhängenden Graph erhalten
    min_nodes_per_level: Dict[str, int] = field(default_factory=lambda: {
        "system": 1,
        "subsystem": 1,
        "component": 2,
        "file": 3,
        "symbol": 5
    })
    
    def validate(self) -> List[str]:
        """Validiert die Budget-Konfiguration."""
        errors = []
        
        if self.token_budget < 0:
            errors.append("token_budget muss >= 0 sein")
        
        if self.node_budget < 0:
            errors.append("node_budget muss >= 0 sein")
        
        if self.edge_budget < 0:
            errors.append("edge_budget muss >= 0 sein")
        
        if not (1 <= self.depth_budget <= 5):
            errors.append("depth_budget muss zwischen 1 und 5 sein")
        
        shares = [
            self.system_token_share,
            self.subsystem_token_share,
            self.component_token_share,
            self.file_token_share,
            self.symbol_token_share
        ]
        
        total_share = sum(shares)
        if abs(total_share - 1.0) > 0.01:
            errors.append(f"Token-Shares summieren zu {total_share}, erwartet ~1.0")
        
        for share_name, share in zip(
            ["system", "subsystem", "component", "file", "symbol"],
            shares
        ):
            if not (0.0 <= share <= 1.0):
                errors.append(f"{share_name}_token_share muss zwischen 0.0 und 1.0 sein")
        
        return errors
    
    def get_level_token_budget(self, level: str) -> int:
        """Berechnet das Token-Budget für ein spezifisches Level."""
        share_map = {
            "system": self.system_token_share,
            "subsystem": self.subsystem_token_share,
            "component": self.component_token_share,
            "file": self.file_token_share,
            "symbol": self.symbol_token_share
        }
        
        share = share_map.get(level, 0.2)
        return int(self.token_budget * share)
    
    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert Policy in Dictionary."""
        return {
            "token_budget": self.token_budget,
            "node_budget": self.node_budget,
            "edge_budget": self.edge_budget,
            "depth_budget": self.depth_budget,
            "system_token_share": self.system_token_share,
            "subsystem_token_share": self.subsystem_token_share,
            "component_token_share": self.component_token_share,
            "file_token_share": self.file_token_share,
            "symbol_token_share": self.symbol_token_share,
            "prefer_higher_levels": self.prefer_higher_levels,
            "preserve_connectivity": self.preserve_connectivity,
            "min_nodes_per_level": self.min_nodes_per_level
        }


@dataclass
class BudgetUsage:
    """Trackt aktuelle Budget-Nutzung."""
    
    tokens: int = 0
    nodes: int = 0
    edges: int = 0
    depth: int = 0
    
    def is_within_limits(self, policy: ArchitectureBudgetPolicy) -> bool:
        """Prüft ob alle Limits eingehalten werden."""
        return (
            self.tokens <= policy.token_budget and
            self.nodes <= policy.node_budget and
            self.edges <= policy.edge_budget and
            self.depth <= policy.depth_budget
        )
    
    def get_exceeded_limits(self, policy: ArchitectureBudgetPolicy) -> List[str]:
        """Listet überschrittene Limits auf."""
        exceeded = []
        
        if self.tokens > policy.token_budget:
            exceeded.append(f"token_budget: {self.tokens}/{policy.token_budget}")
        
        if self.nodes > policy.node_budget:
            exceeded.append(f"node_budget: {self.nodes}/{policy.node_budget}")
        
        if self.edges > policy.edge_budget:
            exceeded.append(f"edge_budget: {self.edges}/{policy.edge_budget}")
        
        if self.depth > policy.depth_budget:
            exceeded.append(f"depth_budget: {self.depth}/{policy.depth_budget}")
        
        return exceeded
    
    def to_dict(self) -> Dict[str, int]:
        """Konvertiert Usage in Dictionary."""
        return {
            "tokens": self.tokens,
            "nodes": self.nodes,
            "edges": self.edges,
            "depth": self.depth
        }


@dataclass
class BudgetDecision:
    """
    Dokumentiert Budget-Entscheidung für maschinelle Nachvollziehbarkeit.
    """
    
    policy: ArchitectureBudgetPolicy
    usage: BudgetUsage
    truncated: bool
    continuation_handle: Optional[str] = None
    pruning_reason: Optional[str] = None
    preserved_levels: List[str] = field(default_factory=list)
    pruned_node_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert Decision in Dictionary."""
        return {
            "policy": self.policy.to_dict(),
            "usage": self.usage.to_dict(),
            "truncated": self.truncated,
            "continuation_handle": self.continuation_handle,
            "pruning_reason": self.pruning_reason,
            "preserved_levels": self.preserved_levels,
            "pruned_node_ids": self.pruned_node_ids
        }


def estimate_token_count(text: str) -> int:
    """
    Schätzt Token-Anzahl für Text.
    Einfache Heuristik: ~4 Zeichen pro Token für Englisch/Code.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def create_default_policy() -> ArchitectureBudgetPolicy:
    """Erstellt Default-Budget-Policy für CodeCompass."""
    return ArchitectureBudgetPolicy(
        token_budget=4000,
        node_budget=100,
        edge_budget=200,
        depth_budget=5,
        prefer_higher_levels=True,
        preserve_connectivity=True
    )


def create_tight_policy() -> ArchitectureBudgetPolicy:
    """Erstellt strikte Budget-Policy für kleine Context Windows."""
    return ArchitectureBudgetPolicy(
        token_budget=2000,
        node_budget=50,
        edge_budget=100,
        depth_budget=4,
        prefer_higher_levels=True,
        preserve_connectivity=True
    )


def create_expanded_policy() -> ArchitectureBudgetPolicy:
    """Erstellt großzügige Budget-Policy für detaillierte Analysen."""
    return ArchitectureBudgetPolicy(
        token_budget=8000,
        node_budget=200,
        edge_budget=400,
        depth_budget=5,
        prefer_higher_levels=False,
        preserve_connectivity=True
    )
