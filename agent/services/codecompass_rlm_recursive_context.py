"""
CodeCompass RLM Recursive Context Integration

Integration des Recursive Language Model (RLM) für hierarchische 
Architektur-Kontext-Generierung mit rekursiver Expansion.

Author: CodeCompass Team
License: MIT
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import logging
import hashlib
import time

logger = logging.getLogger(__name__)


@dataclass
class RLMContextNode:
    """Ein Knoten im RLM-Kontextbaum"""
    node_id: str
    level: str  # system, subsystem, component, file, symbol
    content: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    parent: Optional[str] = None
    relevance_score: float = 0.0
    tokens_used: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "level": self.level,
            "content": self.content[:200],
            "evidence_count": len(self.evidence),
            "children_count": len(self.children),
            "parent": self.parent,
            "relevance_score": self.relevance_score,
            "tokens_used": self.tokens_used
        }


@dataclass
class RLMPlan:
    """Ein RLM-Expansionsplan"""
    plan_id: str
    root_node_id: str
    max_depth: int
    max_tokens: int
    intent: str
    nodes: List[RLMContextNode] = field(default_factory=list)
    expansion_history: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "root_node_id": self.root_node_id,
            "max_depth": self.max_depth,
            "max_tokens": self.max_tokens,
            "intent": self.intent,
            "node_count": len(self.nodes),
            "total_tokens": self.total_tokens,
            "status": self.status,
            "expansion_steps": len(self.expansion_history)
        }


class RLMRecursiveContextEngine:
    """
    RLM Engine für rekursive Kontext-Generierung
    
    Implementiert rekursive Expansion von Architektur-Knoten
    mit Budget-Tracking und Evidence-Preservation.
    """
    
    LEVEL_HIERARCHY = ["system", "subsystem", "component", "file", "symbol"]
    
    def __init__(
        self,
        architecture_slice_service: Any,
        architecture_summary_service: Any,
        llm_client: Optional[Any] = None,
        max_recursion_depth: int = 5,
        max_total_tokens: int = 8000,
        token_budget_per_level: Optional[Dict[str, int]] = None
    ):
        """
        Initialisiere RLM Engine
        
        Args:
            architecture_slice_service: Service für Architektur-Slices
            architecture_summary_service: Service für Summaries
            llm_client: Optionaler LLM-Client für Entscheidungsfindung
            max_recursion_depth: Maximale Rekursionstiefe
            max_total_tokens: Maximale Gesamt-Token
            token_budget_per_level: Token-Budget pro Level
        """
        self.slice_service = architecture_slice_service
        self.summary_service = architecture_summary_service
        self.llm_client = llm_client
        self.max_recursion_depth = max_recursion_depth
        self.max_total_tokens = max_total_tokens
        
        self.token_budget_per_level = token_budget_per_level or {
            "system": 500,
            "subsystem": 1000,
            "component": 2000,
            "file": 3000,
            "symbol": 4000
        }
        
        self.active_plans: Dict[str, RLMPlan] = {}
        self.completed_plans: Dict[str, RLMPlan] = {}
        
        logger.info(
            f"RLMRecursiveContextEngine initialized with "
            f"max_depth={max_recursion_depth}, max_tokens={max_total_tokens}"
        )
    
    def _generate_plan_id(self, root_node_id: str, intent: str) -> str:
        """Generiere deterministische Plan-ID"""
        content = f"{root_node_id}:{intent}:{time.time()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _estimate_tokens(self, text: str) -> int:
        """Schätze Token-Anzahl"""
        return len(text) // 4
    
    def _get_level_depth(self, level: str) -> int:
        """Hole Tiefe eines Levels"""
        try:
            return self.LEVEL_HIERARCHY.index(level)
        except ValueError:
            return 0
    
    def create_plan(
        self,
        root_node_id: str,
        intent: str,
        max_depth: Optional[int] = None,
        max_tokens: Optional[int] = None
    ) -> RLMPlan:
        """
        Erstelle neuen RLM-Expansionsplan
        
        Args:
            root_node_id: Wurzelknoten für Expansion
            intent: User-Intent
            max_depth: Optionale maximale Tiefe
            max_tokens: Optionales maximales Token-Budget
            
        Returns:
            Erstellt RLMPlan
        """
        plan = RLMPlan(
            plan_id=self._generate_plan_id(root_node_id, intent),
            root_node_id=root_node_id,
            max_depth=max_depth or self.max_recursion_depth,
            max_tokens=max_tokens or self.max_total_tokens,
            intent=intent
        )
        
        self.active_plans[plan.plan_id] = plan
        
        logger.info(f"Created RLM plan {plan.plan_id} for {root_node_id}")
        
        return plan
    
    def expand_node(
        self,
        plan: RLMPlan,
        node_id: str,
        current_depth: int = 0
    ) -> bool:
        """
        Expandiere einen Knoten rekursiv
        
        Args:
            plan: Aktueller Expansionsplan
            node_id: Zu expandierender Knoten
            current_depth: Aktuelle Tiefe
            
        Returns:
            True wenn erfolgreich, False wenn Budget überschritten
        """
        # Prüfe Abbruchbedingungen
        if current_depth >= plan.max_depth:
            logger.debug(f"Max depth reached at {current_depth}")
            return False
        
        if plan.total_tokens >= plan.max_tokens:
            logger.debug(f"Max tokens reached: {plan.total_tokens}")
            return False
        
        # Hole Knoten-Informationen
        try:
            node_info = self._fetch_node_info(node_id)
        except Exception as e:
            logger.warning(f"Failed to fetch node {node_id}: {e}")
            return False
        
        # Bestimme Level
        level = node_info.get("level", "component")
        level_depth = self._get_level_depth(level)
        
        # Prüfe Level-spezifisches Budget
        level_budget = self.token_budget_per_level.get(level, 2000)
        level_tokens = sum(
            n.tokens_used for n in plan.nodes if n.level == level
        )
        
        if level_tokens >= level_budget:
            logger.debug(f"Level budget exceeded for {level}")
            return False
        
        # Erstelle Context Node
        context_node = RLMContextNode(
            node_id=node_id,
            level=level,
            content=node_info.get("summary", ""),
            evidence=node_info.get("evidence", []),
            relevance_score=node_info.get("relevance", 0.0)
        )
        
        # Schätze Token
        content_tokens = self._estimate_tokens(context_node.content)
        evidence_tokens = sum(
            self._estimate_tokens(ev.get("content", ""))
            for ev in context_node.evidence
        )
        context_node.tokens_used = content_tokens + evidence_tokens
        
        # Füge zum Plan hinzu
        plan.nodes.append(context_node)
        plan.total_tokens += context_node.tokens_used
        
        # Logge Expansion
        plan.expansion_history.append({
            "step": len(plan.expansion_history) + 1,
            "action": "expand",
            "node_id": node_id,
            "level": level,
            "depth": current_depth,
            "tokens_added": context_node.tokens_used,
            "total_tokens": plan.total_tokens
        })
        
        logger.debug(
            f"Expanded {node_id} ({level}) at depth {current_depth}, "
            f"+{context_node.tokens_used} tokens"
        )
        
        # Hole Kinder für weitere Expansion
        children = node_info.get("children", [])
        
        if children and current_depth < plan.max_depth - 1:
            # Entscheide welche Kinder expandiert werden sollen
            children_to_expand = self._select_children_for_expansion(
                children, plan, level
            )
            
            context_node.children = children_to_expand
            
            # Rekursive Expansion
            for child_id in children_to_expand:
                if not self.expand_node(plan, child_id, current_depth + 1):
                    break
        
        return True
    
    def _fetch_node_info(self, node_id: str) -> Dict[str, Any]:
        """Hole Informationen über einen Knoten"""
        info = {
            "node_id": node_id,
            "level": "component",
            "summary": "",
            "evidence": [],
            "children": [],
            "relevance": 0.0
        }
        
        try:
            # Hole Summary
            summary_result = self.summary_service.get_or_create_summary(
                node_id=node_id,
                max_tokens=200
            )
            info["summary"] = summary_result.get("summary", "")
            info["level"] = summary_result.get("level", "component")
        except Exception as e:
            logger.warning(f"Summary fetch failed for {node_id}: {e}")
        
        try:
            # Hole Slice für Evidence und Kinder
            slice_result = self.slice_service.select_slice(
                seed_node_ids=[node_id],
                max_nodes=10,
                strategy="GRAPH_NEIGHBORHOOD"
            )
            
            # Extrahiere Evidence
            for node in slice_result.get("nodes", []):
                if node.get("evidence_snippets"):
                    info["evidence"].append({
                        "type": "code_snippet",
                        "content": node["evidence_snippets"][0][:500]
                    })
                
                # Extrahiere Kinder (contains-Beziehungen)
                if node.get("relationships"):
                    for rel in node["relationships"]:
                        if rel.get("type") == "contains":
                            info["children"].append(rel["target_id"])
        except Exception as e:
            logger.warning(f"Slice fetch failed for {node_id}: {e}")
        
        return info
    
    def _select_children_for_expansion(
        self,
        children: List[str],
        plan: RLMPlan,
        parent_level: str
    ) -> List[str]:
        """Wähle Kinder für Expansion aus"""
        if not children:
            return []
        
        # Limitiere Anzahl basierend auf verbleibendem Budget
        remaining_tokens = plan.max_tokens - plan.total_tokens
        avg_token_per_node = 500  # Schätzung
        max_children = min(
            len(children),
            max(1, remaining_tokens // avg_token_per_node)
        )
        
        # Wenn Intent bekannt, priorisiere relevante Kinder
        if self.llm_client and plan.intent:
            try:
                # LLM-basierte Selektion
                prompt = f"""
Select most relevant children for intent: {plan.intent}
Parent level: {parent_level}
Children: {children[:10]}

Return list of most relevant child IDs (max {max_children}).
"""
                decision = self.llm_client.decide(prompt)
                selected = decision.get("selected_children", children[:max_children])
                return selected[:max_children]
            except Exception as e:
                logger.warning(f"LLM selection failed: {e}")
        
        # Fallback: Nimm erste max_children
        return children[:max_children]
    
    def execute_plan(self, plan_id: str) -> Dict[str, Any]:
        """
        Führe RLM-Expansionsplan aus
        
        Args:
            plan_id: ID des auszuführenden Plans
            
        Returns:
            Execution results
        """
        if plan_id not in self.active_plans:
            raise ValueError(f"Plan {plan_id} not found")
        
        plan = self.active_plans[plan_id]
        plan.status = "running"
        
        logger.info(f"Executing RLM plan {plan_id}")
        
        start_time = time.time()
        
        # Starte Expansion vom Root-Knoten
        success = self.expand_node(plan, plan.root_node_id, current_depth=0)
        
        execution_time = time.time() - start_time
        
        # Update Status
        plan.status = "completed" if success else "partial"
        
        # Move zu completed
        self.completed_plans[plan_id] = plan
        del self.active_plans[plan_id]
        
        result = {
            "success": success,
            "plan_id": plan_id,
            "root_node": plan.root_node_id,
            "intent": plan.intent,
            "total_nodes": len(plan.nodes),
            "total_tokens": plan.total_tokens,
            "max_tokens": plan.max_tokens,
            "token_utilization": plan.total_tokens / plan.max_tokens if plan.max_tokens > 0 else 0,
            "max_depth_reached": max(
                (self._get_level_depth(n.level) for n in plan.nodes),
                default=0
            ),
            "execution_time_seconds": execution_time,
            "expansion_steps": len(plan.expansion_history),
            "nodes_by_level": self._count_nodes_by_level(plan.nodes),
            "plan": plan.to_dict()
        }
        
        logger.info(
            f"RLM plan {plan_id} completed: {result['total_nodes']} nodes, "
            f"{result['total_tokens']} tokens in {execution_time:.2f}s"
        )
        
        return result
    
    def _count_nodes_by_level(
        self,
        nodes: List[RLMContextNode]
    ) -> Dict[str, int]:
        """Zähle Nodes pro Level"""
        counts = {}
        for node in nodes:
            level = node.level
            counts[level] = counts.get(level, 0) + 1
        return counts
    
    def get_context_for_plan(self, plan_id: str) -> Dict[str, Any]:
        """
        Generiere finalen Kontext für einen Plan
        
        Args:
            plan_id: Plan-ID
            
        Returns:
            Strukturierter Kontext für LLM-Consumption
        """
        if plan_id not in self.completed_plans:
            if plan_id not in self.active_plans:
                raise ValueError(f"Plan {plan_id} not found")
            plan = self.active_plans[plan_id]
        else:
            plan = self.completed_plans[plan_id]
        
        # Sortiere Nodes nach Hierarchie
        sorted_nodes = sorted(
            plan.nodes,
            key=lambda n: self._get_level_depth(n.level)
        )
        
        # Baue kontextuelle Struktur
        context = {
            "plan_id": plan_id,
            "intent": plan.intent,
            "total_tokens": plan.total_tokens,
            "hierarchy": {},
            "flat_evidence": [],
            "summaries": []
        }
        
        for node in sorted_nodes:
            level = node.level
            
            if level not in context["hierarchy"]:
                context["hierarchy"][level] = []
            
            context["hierarchy"][level].append({
                "node_id": node.node_id,
                "content": node.content,
                "relevance": node.relevance_score
            })
            
            if node.evidence:
                context["flat_evidence"].extend(node.evidence)
            
            if node.content:
                context["summaries"].append({
                    "node_id": node.node_id,
                    "level": level,
                    "summary": node.content
                })
        
        return context
    
    def merge_contexts(
        self,
        plan_ids: List[str],
        max_total_tokens: int = 8000
    ) -> Dict[str, Any]:
        """
        Merge mehrere RLM-Kontexte
        
        Args:
            plan_ids: Liste von Plan-IDs zu mergen
            max_total_tokens: Maximale Gesamt-Token
            
        Returns:
            Gemergter Kontext
        """
        all_nodes = []
        total_tokens = 0
        
        for plan_id in plan_ids:
            if plan_id in self.completed_plans:
                plan = self.completed_plans[plan_id]
            elif plan_id in self.active_plans:
                plan = self.active_plans[plan_id]
            else:
                logger.warning(f"Plan {plan_id} not found, skipping")
                continue
            
            for node in plan.nodes:
                if total_tokens + node.tokens_used <= max_total_tokens:
                    all_nodes.append(node)
                    total_tokens += node.tokens_used
        
        # Generiere gemergten Kontext
        merged_context = {
            "source_plans": plan_ids,
            "total_nodes": len(all_nodes),
            "total_tokens": total_tokens,
            "max_tokens": max_total_tokens,
            "nodes": [n.to_dict() for n in all_nodes],
            "merged_at": time.time()
        }
        
        logger.info(
            f"Merged {len(plan_ids)} plans into {len(all_nodes)} nodes, "
            f"{total_tokens} tokens"
        )
        
        return merged_context


def create_rlm_context_engine(
    architecture_slice_service: Any,
    architecture_summary_service: Any,
    llm_client: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None
) -> RLMRecursiveContextEngine:
    """
    Factory-Funktion zum Erstellen einer RLM Engine
    
    Args:
        architecture_slice_service: Instanz des ArchitectureSliceService
        architecture_summary_service: Instanz des ArchitectureSummaryService
        llm_client: Optionaler LLM-Client
        config: Optionale Konfiguration
        
    Returns:
        Konfigurierte RLMRecursiveContextEngine
    """
    config = config or {}
    
    return RLMRecursiveContextEngine(
        architecture_slice_service=architecture_slice_service,
        architecture_summary_service=architecture_summary_service,
        llm_client=llm_client,
        max_recursion_depth=config.get("max_recursion_depth", 5),
        max_total_tokens=config.get("max_total_tokens", 8000),
        token_budget_per_level=config.get("token_budget_per_level")
    )
