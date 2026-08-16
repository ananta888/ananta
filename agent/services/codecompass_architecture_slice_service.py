"""
CodeCompass Architecture Slice Service

Selects query-relevant architecture slices from the hierarchical projection.
Combines query relevance, graph neighborhood, node level, trust/evidence,
and budget costs for optimal slice selection.
"""

from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
import re

logger = logging.getLogger(__name__)


class SliceSelectionStrategy(Enum):
    """Strategies for selecting architecture slices."""
    QUERY_RELEVANCE = "query_relevance"
    GRAPH_NEIGHBORHOOD = "graph_neighborhood"
    LEVEL_PRIORITY = "level_priority"
    TRUST_BASED = "trust_based"
    BUDGET_OPTIMIZED = "budget_optimized"
    HYBRID = "hybrid"


@dataclass
class ScoredNode:
    """A node with relevance score for slice selection."""
    node_id: str
    level: str
    title: str
    relevance_score: float
    trust_score: float
    budget_cost: int
    evidence_count: int
    is_seed: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "level": self.level,
            "title": self.title,
            "relevance_score": self.relevance_score,
            "trust_score": self.trust_score,
            "budget_cost": self.budget_cost,
            "evidence_count": self.evidence_count,
            "is_seed": self.is_seed
        }


@dataclass
class ArchitectureSlice:
    """Selected architecture slice for a query."""
    query: str
    nodes: Dict[str, ScoredNode] = field(default_factory=dict)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    total_budget_used: int = 0
    node_count: int = 0
    edge_count: int = 0
    truncation_reason: Optional[str] = None
    continuation_handle: Optional[str] = None
    expandable_nodes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": self.edges,
            "total_budget_used": self.total_budget_used,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "truncation_reason": self.truncation_reason,
            "continuation_handle": self.continuation_handle,
            "expandable_nodes": self.expandable_nodes
        }


class ArchitectureSliceService:
    """Service for selecting query-relevant architecture slices."""
    
    # Level weights for scoring (higher = more important for system questions)
    LEVEL_WEIGHTS = {
        "system": 1.0,
        "subsystem": 0.9,
        "component": 0.8,
        "file": 0.6,
        "symbol": 0.4,
        "unknown": 0.2
    }
    
    # Trust scores
    TRUST_SCORES = {
        "verified": 1.0,
        "derived": 0.7,
        "unknown": 0.3
    }
    
    def __init__(self, budget_policy: Any, projector: Any):
        """
        Initialize slice service.
        
        Args:
            budget_policy: Architecture budget policy instance
            projector: Hierarchical architecture projector
        """
        self.budget_policy = budget_policy
        self.projector = projector
    
    def select_slice(
        self,
        query: str,
        scope: str,
        revision: str,
        tenant: str,
        strategy: SliceSelectionStrategy = SliceSelectionStrategy.HYBRID,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
        max_tokens: Optional[int] = None
    ) -> ArchitectureSlice:
        """
        Select a query-relevant architecture slice.
        
        Args:
            query: User query
            scope: Root scope
            revision: Repository revision
            tenant: Tenant identifier
            strategy: Selection strategy
            max_nodes: Maximum nodes in slice
            max_edges: Maximum edges in slice
            max_tokens: Maximum tokens in slice
            
        Returns:
            ArchitectureSlice with selected nodes and edges
        """
        # Get full projection
        projection = self.projector.project(scope, revision, tenant)
        
        # Get budget limits
        budget_limits = self.budget_policy.get_limits(
            query=query,
            max_nodes=max_nodes,
            max_edges=max_edges,
            max_tokens=max_tokens
        )
        
        # Score all nodes
        scored_nodes = self._score_nodes(
            projection.nodes.values(),
            query,
            strategy
        )
        
        # Select top nodes within budget
        selected_nodes = self._select_within_budget(
            scored_nodes,
            budget_limits
        )
        
        # Build edges between selected nodes
        selected_edges = self._select_edges(
            projection.edges,
            set(n.node_id for n in selected_nodes)
        )
        
        # Create slice
        slice_result = ArchitectureSlice(
            query=query,
            nodes={n.node_id: n for n in selected_nodes},
            edges=selected_edges,
            total_budget_used=sum(n.budget_cost for n in selected_nodes),
            node_count=len(selected_nodes),
            edge_count=len(selected_edges)
        )
        
        # Check for truncation
        if len(scored_nodes) > len(selected_nodes):
            slice_result.truncation_reason = "budget_limit"
            slice_result.continuation_handle = self._generate_continuation_handle(
                query, scope, revision, len(selected_nodes)
            )
        
        # Identify expandable nodes
        slice_result.expandable_nodes = [
            n.node_id for n in selected_nodes
            if projection.nodes[n.node_id].expandable
        ]
        
        logger.info(
            f"Selected slice with {slice_result.node_count} nodes and "
            f"{slice_result.edge_count} edges for query: {query[:50]}..."
        )
        
        return slice_result
    
    def _score_nodes(
        self,
        nodes: Any,
        query: str,
        strategy: SliceSelectionStrategy
    ) -> List[ScoredNode]:
        """Score nodes based on selection strategy."""
        scored = []
        
        for node in nodes:
            # Calculate relevance score
            relevance = self._calculate_query_relevance(node, query)
            
            # Calculate trust score
            trust = self.TRUST_SCORES.get(node.trust, 0.3)
            
            # Estimate budget cost (tokens)
            budget_cost = self._estimate_token_cost(node)
            
            # Count evidence
            evidence_count = len(node.evidence_hashes)
            
            scored_node = ScoredNode(
                node_id=node.id,
                level=node.level.value,
                title=node.title,
                relevance_score=relevance,
                trust_score=trust,
                budget_cost=budget_cost,
                evidence_count=evidence_count,
                is_seed=self._is_seed_node(node, query)
            )
            
            scored.append(scored_node)
        
        # Sort by combined score based on strategy
        if strategy == SliceSelectionStrategy.QUERY_RELEVANCE:
            scored.sort(key=lambda x: x.relevance_score, reverse=True)
        elif strategy == SliceSelectionStrategy.TRUST_BASED:
            scored.sort(key=lambda x: x.trust_score, reverse=True)
        elif strategy == SliceSelectionStrategy.BUDGET_OPTIMIZED:
            scored.sort(key=lambda x: x.relevance_score / max(x.budget_cost, 1), reverse=True)
        else:  # HYBRID
            scored.sort(
                key=lambda x: (
                    x.relevance_score * 0.4 +
                    x.trust_score * 0.3 +
                    (1.0 / max(x.budget_cost, 1)) * 0.2 +
                    (1.0 if x.is_seed else 0.0) * 0.1
                ),
                reverse=True
            )
        
        return scored
    
    def _calculate_query_relevance(self, node: Any, query: str) -> float:
        """Calculate relevance score between node and query."""
        query_lower = query.lower()
        node_text = f"{node.title} {node.short_summary} {' '.join(node.responsibilities)}".lower()
        
        # Simple keyword matching
        query_words = set(query_lower.split())
        node_words = set(re.findall(r'\w+', node_text))
        
        # Word overlap
        overlap = len(query_words & node_words)
        word_score = overlap / max(len(query_words), 1)
        
        # Substring matching
        substring_matches = sum(1 for word in query_words if word in node_text)
        substring_score = substring_matches / max(len(query_words), 1)
        
        # Level-based boost for system questions
        level_boost = self.LEVEL_WEIGHTS.get(node.level.value, 0.2)
        
        # System question detection
        is_system_question = any(
            kw in query_lower for kw in ["what is", "system", "architecture", "overview"]
        )
        if is_system_question and node.level.value in ["system", "subsystem"]:
            level_boost *= 1.5
        
        return min(1.0, (word_score * 0.4 + substring_score * 0.4 + level_boost * 0.2))
    
    def _is_seed_node(self, node: Any, query: str) -> bool:
        """Check if node should be a seed for graph expansion."""
        query_lower = query.lower()
        return (
            node.level.value in ["system", "subsystem"] or
            any(kw in node.title.lower() for kw in query_lower.split())
        )
    
    def _estimate_token_cost(self, node: Any) -> int:
        """Estimate token cost for including a node."""
        # Rough estimation: ~4 chars per token
        text_length = (
            len(node.title) +
            len(node.short_summary) +
            sum(len(r) for r in node.responsibilities)
        )
        return max(10, text_length // 4)
    
    def _select_within_budget(
        self,
        scored_nodes: List[ScoredNode],
        budget_limits: Dict[str, int]
    ) -> List[ScoredNode]:
        """Select nodes within budget constraints."""
        selected = []
        total_cost = 0
        node_count = 0
        
        max_nodes = budget_limits.get("max_nodes", 50)
        max_tokens = budget_limits.get("max_tokens", 2000)
        
        for node in scored_nodes:
            # Check node count limit
            if node_count >= max_nodes:
                break
            
            # Check token budget
            if total_cost + node.budget_cost > max_tokens:
                continue
            
            # Always include seed nodes
            if not node.is_seed and node_count >= 5:
                # Non-seed nodes after first 5 need higher relevance
                if node.relevance_score < 0.3:
                    continue
            
            selected.append(node)
            total_cost += node.budget_cost
            node_count += 1
        
        return selected
    
    def _select_edges(
        self,
        all_edges: List[Any],
        selected_node_ids: Set[str]
    ) -> List[Dict[str, Any]]:
        """Select edges between selected nodes."""
        selected_edges = []
        
        for edge in all_edges:
            if edge.source_id in selected_node_ids and edge.target_id in selected_node_ids:
                selected_edges.append(edge.to_dict())
        
        return selected_edges
    
    def _generate_continuation_handle(
        self,
        query: str,
        scope: str,
        revision: str,
        offset: int
    ) -> str:
        """Generate handle for continuing/expanding slice."""
        import hashlib
        import base64
        
        handle_data = f"{query}|{scope}|{revision}|{offset}"
        hash_bytes = hashlib.sha256(handle_data.encode()).digest()
        return base64.urlsafe_b64encode(hash_bytes[:16]).decode().rstrip('=')
    
    def expand_node(
        self,
        node_id: str,
        continuation_handle: str,
        max_depth: int = 1,
        max_nodes: int = 10
    ) -> ArchitectureSlice:
        """
        Expand a specific node in the slice.
        
        Args:
            node_id: Node to expand
            continuation_handle: Valid continuation handle
            max_depth: Maximum depth to expand
            max_nodes: Maximum nodes to add
            
        Returns:
            ArchitectureSlice with expanded nodes
        """
        # Validate continuation handle
        if not self._validate_continuation_handle(continuation_handle):
            raise ValueError("Invalid or expired continuation handle")
        
        # Get neighbors of node
        # Implementation depends on graph store
        neighbors = self._get_node_neighbors(node_id, max_depth, max_nodes)
        
        # Score and select neighbors
        scored_neighbors = self._score_nodes(
            neighbors,
            "",  # No query, already contextually relevant
            SliceSelectionStrategy.GRAPH_NEIGHBORHOOD
        )
        
        # Create expansion slice
        expansion = ArchitectureSlice(
            query=f"Expansion of {node_id}",
            nodes={n.node_id: n for n in scored_neighbors[:max_nodes]},
            edges=[],
            node_count=len(scored_neighbors[:max_nodes])
        )
        
        return expansion
    
    def _validate_continuation_handle(self, handle: str) -> bool:
        """Validate continuation handle."""
        # Basic validation - in production, check expiration etc.
        return len(handle) > 10 and handle.isascii()
    
    def _get_node_neighbors(
        self,
        node_id: str,
        max_depth: int,
        max_nodes: int
    ) -> List[Any]:
        """Get neighboring nodes from graph."""
        # Delegate to projector/graph store
        if hasattr(self.projector, 'get_neighbors'):
            return self.projector.get_neighbors(node_id, max_depth, max_nodes)
        
        # Fallback: empty list
        return []


def create_slice_service(
    budget_policy: Any,
    projector: Any
) -> ArchitectureSliceService:
    """Factory function to create slice service."""
    return ArchitectureSliceService(budget_policy, projector)
