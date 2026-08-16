"""
CodeCompass Hierarchical Architecture Projection

Projects the existing CodeCompass graph into stable abstraction levels:
system -> subsystem -> component -> file -> symbol

This is a reproducible view/projection, not a second persistent truth.
"""

from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import logging

logger = logging.getLogger(__name__)


class ArchitectureLevel(Enum):
    """Hierarchical architecture levels."""
    SYSTEM = "system"
    SUBSYSTEM = "subsystem"
    COMPONENT = "component"
    FILE = "file"
    SYMBOL = "symbol"
    UNKNOWN = "unknown"


@dataclass
class ArchitectureNode:
    """A node in the hierarchical architecture projection."""
    id: str
    level: ArchitectureLevel
    title: str
    short_summary: str
    responsibilities: List[str] = field(default_factory=list)
    source_refs: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    trust: str = "verified"  # verified, derived, unknown
    expandable: bool = True
    evidence_hashes: List[str] = field(default_factory=list)
    revision: str = ""
    tenant: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level.value,
            "title": self.title,
            "short_summary": self.short_summary,
            "responsibilities": self.responsibilities,
            "source_refs": self.source_refs,
            "confidence": self.confidence,
            "trust": self.trust,
            "expandable": self.expandable,
            "evidence_hashes": self.evidence_hashes,
            "revision": self.revision,
            "tenant": self.tenant
        }


@dataclass
class ArchitectureEdge:
    """An edge in the hierarchical architecture projection."""
    source_id: str
    target_id: str
    relationship_type: str
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    
    ALLOWED_RELATIONSHIPS = {
        "contains", "uses", "calls", "depends_on", 
        "provides_context_to", "implements", "exposes_tool",
        "stores", "retrieves_from", "governed_by",
        "extends", "composes", "references"
    }
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type,
            "evidence_refs": self.evidence_refs,
            "confidence": self.confidence
        }
    
    def validate(self) -> bool:
        """Validate edge relationship type."""
        return self.relationship_type in self.ALLOWED_RELATIONSHIPS


@dataclass
class HierarchicalArchitectureProjection:
    """Complete hierarchical architecture projection."""
    nodes: Dict[str, ArchitectureNode] = field(default_factory=dict)
    edges: List[ArchitectureEdge] = field(default_factory=list)
    root_scope: str = ""
    revision: str = ""
    tenant: str = ""
    generated_at: str = ""
    projection_hash: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_scope": self.root_scope,
            "revision": self.revision,
            "tenant": self.tenant,
            "generated_at": self.generated_at,
            "projection_hash": self.projection_hash,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges]
        }


class HierarchicalArchitectureProjector:
    """Projects CodeCompass graph into hierarchical architecture."""
    
    LEVEL_ORDER = {
        ArchitectureLevel.SYSTEM: 0,
        ArchitectureLevel.SUBSYSTEM: 1,
        ArchitectureLevel.COMPONENT: 2,
        ArchitectureLevel.FILE: 3,
        ArchitectureLevel.SYMBOL: 4,
        ArchitectureLevel.UNKNOWN: 5
    }
    
    def __init__(self, graph_store: Any):
        """
        Initialize projector with graph store.
        
        Args:
            graph_store: CodeCompass graph store (JSON or SQLite)
        """
        self.graph_store = graph_store
        self._node_cache: Dict[str, ArchitectureNode] = {}
        self._edge_cache: List[ArchitectureEdge] = []
    
    def project(
        self,
        scope: str,
        revision: str,
        tenant: str
    ) -> HierarchicalArchitectureProjection:
        """
        Project graph into hierarchical architecture.
        
        Args:
            scope: Root scope for projection
            revision: Repository revision
            tenant: Tenant identifier
            
        Returns:
            HierarchicalArchitectureProjection
        """
        self._node_cache.clear()
        self._edge_cache.clear()
        
        # Fetch graph data from store
        graph_data = self._fetch_graph_data(scope, revision, tenant)
        
        # Classify nodes into levels
        self._classify_nodes(graph_data)
        
        # Build edges between classified nodes
        self._build_edges(graph_data)
        
        # Generate projection hash
        projection = HierarchicalArchitectureProjection(
            nodes=self._node_cache,
            edges=self._edge_cache,
            root_scope=scope,
            revision=revision,
            tenant=tenant,
            generated_at=self._get_timestamp(),
            projection_hash=self._compute_projection_hash()
        )
        
        logger.info(
            f"Projected {len(projection.nodes)} nodes and "
            f"{len(projection.edges)} edges for scope {scope}"
        )
        
        return projection
    
    def _fetch_graph_data(
        self,
        scope: str,
        revision: str,
        tenant: str
    ) -> Dict[str, Any]:
        """Fetch graph data from store."""
        # Delegate to graph store - implementation depends on store type
        if hasattr(self.graph_store, 'fetch_architecture_data'):
            return self.graph_store.fetch_architecture_data(scope, revision, tenant)
        
        # Fallback: fetch raw graph and process
        return {
            "scope": scope,
            "revision": revision,
            "tenant": tenant,
            "nodes": [],
            "edges": []
        }
    
    def _classify_nodes(self, graph_data: Dict[str, Any]) -> None:
        """Classify graph nodes into architecture levels."""
        for node in graph_data.get("nodes", []):
            level = self._determine_level(node)
            arch_node = self._create_architecture_node(node, level)
            self._node_cache[arch_node.id] = arch_node
    
    def _determine_level(self, node: Dict[str, Any]) -> ArchitectureLevel:
        """Determine architecture level for a graph node."""
        node_type = node.get("type", "").lower()
        path = node.get("path", "")
        name = node.get("name", "")
        
        # System level: repository/workspace root
        if node_type in ["repository", "workspace", "system"] or not path:
            return ArchitectureLevel.SYSTEM
        
        # Subsystem level: directories with specific patterns
        if self._is_subsystem_directory(path, node_type):
            return ArchitectureLevel.SUBSYSTEM
        
        # Component level: modules/packages/components
        if self._is_component(path, node_type):
            return ArchitectureLevel.COMPONENT
        
        # File level: actual files
        if node_type in ["file", "module"] or "." in name:
            return ArchitectureLevel.FILE
        
        # Symbol level: functions, classes, variables
        if node_type in ["function", "class", "method", "variable", "symbol"]:
            return ArchitectureLevel.SYMBOL
        
        # Unknown if unclassifiable
        return ArchitectureLevel.UNKNOWN
    
    def _is_subsystem_directory(self, path: str, node_type: str) -> bool:
        """Check if path represents a subsystem directory."""
        subsystem_patterns = [
            "agent/", "worker/", "frontend/", "backend/",
            "services/", "retrieval/", "storage/",
            "src/", "lib/", "packages/"
        ]
        return any(pattern in path for pattern in subsystem_patterns)
    
    def _is_component(self, path: str, node_type: str) -> bool:
        """Check if path represents a component."""
        component_indicators = [
            node_type in ["component", "module", "package"],
            "/" in path and path.count("/") >= 2,
            any(x in path for x in ["_service", "_controller", "_handler"])
        ]
        return any(component_indicators)
    
    def _create_architecture_node(
        self,
        node: Dict[str, Any],
        level: ArchitectureLevel
    ) -> ArchitectureNode:
        """Create architecture node from graph node."""
        node_id = node.get("id", self._generate_node_id(node))
        
        # Extract metadata
        title = node.get("name", node.get("title", "Unknown"))
        summary = node.get("summary", node.get("description", ""))
        responsibilities = node.get("responsibilities", [])
        
        # If no summary, generate one based on level
        if not summary:
            summary = self._generate_summary(node, level)
        
        # Source references
        source_refs = []
        if "path" in node:
            source_refs.append({
                "type": "file_path",
                "path": node["path"],
                "revision": node.get("revision", "")
            })
        
        # Evidence hashes
        evidence_hashes = node.get("evidence_hashes", [])
        
        return ArchitectureNode(
            id=node_id,
            level=level,
            title=title,
            short_summary=summary[:200],  # Limit summary length
            responsibilities=responsibilities,
            source_refs=source_refs,
            confidence=node.get("confidence", 1.0),
            trust=node.get("trust", "verified"),
            expandable=level != ArchitectureLevel.SYMBOL,
            evidence_hashes=evidence_hashes,
            revision=node.get("revision", ""),
            tenant=node.get("tenant", "")
        )
    
    def _generate_summary(
        self,
        node: Dict[str, Any],
        level: ArchitectureLevel
    ) -> str:
        """Generate summary for node based on level."""
        name = node.get("name", "Unknown")
        path = node.get("path", "")
        node_type = node.get("type", "element")
        
        if level == ArchitectureLevel.SYSTEM:
            return f"System root containing all components and subsystems"
        elif level == ArchitectureLevel.SUBSYSTEM:
            return f"Subsystem organizing related components under {path.split('/')[0] if '/' in path else 'root'}"
        elif level == ArchitectureLevel.COMPONENT:
            return f"Component implementing {node_type} functionality"
        elif level == ArchitectureLevel.FILE:
            return f"Source file at {path}"
        elif level == ArchitectureLevel.SYMBOL:
            return f"{node_type.capitalize()} definition in {path}"
        else:
            return f"Unclassified element: {name}"
    
    def _build_edges(self, graph_data: Dict[str, Any]) -> None:
        """Build edges between architecture nodes."""
        for edge in graph_data.get("edges", []):
            source_id = edge.get("source", edge.get("source_id", ""))
            target_id = edge.get("target", edge.get("target_id", ""))
            rel_type = edge.get("type", edge.get("relationship", "references"))
            
            # Validate relationship type
            if rel_type not in ArchitectureEdge.ALLOWED_RELATIONSHIPS:
                rel_type = "references"  # Default to safe relationship
            
            arch_edge = ArchitectureEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=rel_type,
                evidence_refs=edge.get("evidence_refs", []),
                confidence=edge.get("confidence", 1.0)
            )
            
            if arch_edge.validate():
                self._edge_cache.append(arch_edge)
            else:
                logger.warning(f"Invalid edge relationship: {rel_type}")
    
    def _generate_node_id(self, node: Dict[str, Any]) -> str:
        """Generate stable node ID."""
        key_parts = [
            node.get("path", ""),
            node.get("name", ""),
            node.get("type", ""),
            node.get("revision", "")
        ]
        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]
    
    def _compute_projection_hash(self) -> str:
        """Compute hash of entire projection for caching/validation."""
        content = str(sorted(self._node_cache.keys())) + str(len(self._edge_cache))
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _get_timestamp(self) -> str:
        """Get current timestamp."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"


def create_projector(graph_store: Any) -> HierarchicalArchitectureProjector:
    """Factory function to create projector."""
    return HierarchicalArchitectureProjector(graph_store)
