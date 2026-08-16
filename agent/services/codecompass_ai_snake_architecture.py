"""
CodeCompass AI-Snake Architecture Integration

AI-Snake Integration für hierarchische Architektur-Exploration.
Unterstützt autonome Navigation durch Architektur-Hierarchien mit
zielgerichteter Kontext-Sammlung.

Author: CodeCompass Team
License: MIT
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import logging
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class SnakeState:
    """Zustand des AI-Snake für Architektur-Exploration"""
    current_node_id: str
    visited_nodes: List[str] = field(default_factory=list)
    collected_evidence: List[Dict[str, Any]] = field(default_factory=list)
    path_history: List[str] = field(default_factory=list)
    budget_used: int = 0
    budget_max: int = 10000
    intent: str = ""
    depth: int = 0
    max_depth: int = 5
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_node_id": self.current_node_id,
            "visited_nodes": self.visited_nodes,
            "collected_evidence_count": len(self.collected_evidence),
            "path_history": self.path_history,
            "budget_used": self.budget_used,
            "budget_max": self.budget_max,
            "intent": self.intent,
            "depth": self.depth,
            "max_depth": self.max_depth
        }


@dataclass
class SnakeMove:
    """Eine Bewegung des AI-Snake"""
    from_node: str
    to_node: str
    relationship_type: str
    evidence_collected: List[Dict[str, Any]] = field(default_factory=list)
    tokens_consumed: int = 0
    reasoning: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_node": self.from_node,
            "to_node": self.to_node,
            "relationship_type": self.relationship_type,
            "evidence_count": len(self.evidence_collected),
            "tokens_consumed": self.tokens_consumed,
            "reasoning": self.reasoning
        }


class AISnakeArchitectureExplorer:
    """
    AI-Snake für autonome Architektur-Exploration
    
    Navigiert durch die Hierarchie (system → subsystem → component → file → symbol)
    und sammelt relevante Evidenzen basierend auf dem User-Intent.
    """
    
    ALLOWED_RELATIONSHIPS = {
        "contains", "implements", "depends_on", "calls", "imports",
        "extends", "composes", "aggregates", "references", "instantiates",
        "overrides", "documents", "tests", "deploys_to"
    }
    
    def __init__(
        self,
        architecture_slice_service: Any,
        architecture_summary_service: Any,
        llm_client: Optional[Any] = None,
        max_budget: int = 10000,
        max_depth: int = 5
    ):
        """
        Initialisiere AI-Snake Explorer
        
        Args:
            architecture_slice_service: Service für Architektur-Slices
            architecture_summary_service: Service für Summaries
            llm_client: Optionaler LLM-Client für Entscheidungsfindung
            max_budget: Maximale Token-Budget
            max_depth: Maximale Explorations-Tiefe
        """
        self.slice_service = architecture_slice_service
        self.summary_service = architecture_summary_service
        self.llm_client = llm_client
        self.max_budget = max_budget
        self.max_depth = max_depth
        
        logger.info(
            f"AISnakeArchitectureExplorer initialized with budget={max_budget}, "
            f"max_depth={max_depth}"
        )
    
    def _generate_state_id(self, state: SnakeState) -> str:
        """Generiere deterministische State-ID"""
        content = f"{state.current_node_id}:{state.depth}:{len(state.visited_nodes)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _estimate_tokens(self, text: str) -> int:
        """Schätze Token-Anzahl für Text"""
        return len(text) // 4
    
    def _decide_next_move(
        self,
        state: SnakeState,
        available_neighbors: List[Dict[str, Any]]
    ) -> Optional[Tuple[str, str]]:
        """
        Entscheide nächste Bewegung basierend auf Intent und verfügbaren Nachbarn
        
        Returns:
            Tuple (target_node_id, relationship_type) oder None wenn keine Bewegung
        """
        if not available_neighbors:
            return None
        
        # Filter bereits besuchte Nodes
        unvisited = [
            n for n in available_neighbors
            if n["node_id"] not in state.visited_nodes
        ]
        
        if not unvisited:
            logger.debug("Alle Nachbarn bereits besucht")
            return None
        
        # Wenn LLM verfügbar, nutze für intelligente Entscheidung
        if self.llm_client and state.intent:
            try:
                prompt = self._build_decision_prompt(state, unvisited)
                decision = self.llm_client.decide(prompt)
                target_id = decision.get("target_node_id")
                rel_type = decision.get("relationship_type", "contains")
                
                if target_id and rel_type in self.ALLOWED_RELATIONSHIPS:
                    return (target_id, rel_type)
            except Exception as e:
                logger.warning(f"LLM decision failed: {e}, using fallback")
        
        # Fallback: Heuristische Auswahl
        return self._heuristic_selection(state, unvisited)
    
    def _build_decision_prompt(
        self,
        state: SnakeState,
        neighbors: List[Dict[str, Any]]
    ) -> str:
        """Baue Prompt für LLM-Entscheidungsfindung"""
        neighbor_summaries = "\n".join([
            f"- {n['node_id']} ({n.get('level', 'unknown')}): "
            f"{n.get('summary', 'No summary')[:100]}"
            for n in neighbors[:5]  # Max 5 für Context-Limit
        ])
        
        prompt = f"""
Current Architecture Node: {state.current_node_id}
Depth: {state.depth}/{self.max_depth}
Budget Used: {state.budget_used}/{self.max_budget}
Intent: {state.intent}

Available Neighbors:
{neighbor_summaries}

Choose the most relevant node to explore next based on the intent.
Return JSON: {{"target_node_id": "...", "relationship_type": "..."}}
"""
        return prompt
    
    def _heuristic_selection(
        self,
        state: SnakeState,
        neighbors: List[Dict[str, Any]]
    ) -> Optional[Tuple[str, str]]:
        """Heuristische Neighbor-Auswahl ohne LLM"""
        if not neighbors:
            return None
        
        # Priorisiere nach Level (tiefer für Detail-Exploration)
        level_priority = {"symbol": 5, "file": 4, "component": 3, "subsystem": 2, "system": 1}
        
        if "detail" in state.intent.lower() or "implement" in state.intent.lower():
            # Suche tiefere Level
            sorted_neighbors = sorted(
                neighbors,
                key=lambda n: level_priority.get(n.get("level", "component"), 3),
                reverse=True
            )
        elif "overview" in state.intent.lower() or "architecture" in state.intent.lower():
            # Suche höhere Level
            sorted_neighbors = sorted(
                neighbors,
                key=lambda n: level_priority.get(n.get("level", "component"), 3)
            )
        else:
            # Standard: enthält-Beziehungen priorisieren
            contains_first = [
                n for n in neighbors
                if n.get("relationship_type") == "contains"
            ]
            others = [
                n for n in neighbors
                if n.get("relationship_type") != "contains"
            ]
            sorted_neighbors = contains_first + others
        
        best = sorted_neighbors[0]
        return (best["node_id"], best.get("relationship_type", "contains"))
    
    def _collect_evidence(
        self,
        node_id: str,
        state: SnakeState
    ) -> List[Dict[str, Any]]:
        """Sammle Evidenz für aktuellen Node"""
        evidence_list = []
        
        try:
            # Hole Summary
            summary_result = self.summary_service.get_or_create_summary(
                node_id=node_id,
                max_tokens=200
            )
            
            if summary_result.get("summary"):
                evidence_list.append({
                    "type": "summary",
                    "node_id": node_id,
                    "content": summary_result["summary"],
                    "tokens": self._estimate_tokens(summary_result["summary"])
                })
            
            # Hole Architecture Slice für Kontext
            slice_result = self.slice_service.select_slice(
                seed_node_ids=[node_id],
                max_nodes=5,
                strategy="GRAPH_NEIGHBORHOOD"
            )
            
            if slice_result.get("nodes"):
                for node in slice_result["nodes"][:3]:  # Max 3 für Budget
                    if node.get("evidence_snippets"):
                        evidence_list.append({
                            "type": "code_snippet",
                            "node_id": node["node_id"],
                            "content": node["evidence_snippets"][0][:500],
                            "tokens": self._estimate_tokens(node["evidence_snippets"][0][:500])
                        })
        
        except Exception as e:
            logger.warning(f"Evidence collection failed for {node_id}: {e}")
        
        return evidence_list
    
    def explore(
        self,
        start_node_id: str,
        intent: str,
        initial_budget: int = 10000
    ) -> Dict[str, Any]:
        """
        Führe Architektur-Exploration mit AI-Snake durch
        
        Args:
            start_node_id: Startknoten für Exploration
            intent: User-Intent für zielgerichtete Exploration
            initial_budget: Initiales Token-Budget
            
        Returns:
            Dict mit exploration_results, state, moves
        """
        state = SnakeState(
            current_node_id=start_node_id,
            budget_max=initial_budget,
            intent=intent,
            max_depth=self.max_depth
        )
        
        moves: List[SnakeMove] = []
        all_evidence: List[Dict[str, Any]] = []
        
        logger.info(f"Starting AI-Snake exploration from {start_node_id}")
        
        while (
            state.budget_used < state.budget_max and
            state.depth < state.max_depth
        ):
            # Markiere aktuellen Node als besucht
            state.visited_nodes.append(state.current_node_id)
            state.path_history.append(state.current_node_id)
            
            # Sammle Evidenz am aktuellen Node
            evidence = self._collect_evidence(state.current_node_id, state)
            state.collected_evidence.extend(evidence)
            all_evidence.extend(evidence)
            
            # Update Budget
            for ev in evidence:
                state.budget_used += ev.get("tokens", 0)
            
            # Hole verfügbare Nachbarn
            try:
                neighbors = self.slice_service.get_neighbors(
                    node_id=state.current_node_id
                )
            except Exception as e:
                logger.warning(f"Failed to get neighbors: {e}")
                break
            
            # Entscheide nächste Bewegung
            move_decision = self._decide_next_move(state, neighbors)
            
            if not move_decision:
                logger.info("No valid move found, ending exploration")
                break
            
            target_node, rel_type = move_decision
            
            # Erstelle Move
            move = SnakeMove(
                from_node=state.current_node_id,
                to_node=target_node,
                relationship_type=rel_type,
                evidence_collected=evidence,
                tokens_consumed=sum(ev.get("tokens", 0) for ev in evidence),
                reasoning=f"Moving via {rel_type} to explore {intent}"
            )
            moves.append(move)
            
            # Update State
            state.current_node_id = target_node
            state.depth += 1
            
            logger.debug(
                f"Move {len(moves)}: {state.current_node_id} "
                f"(depth={state.depth}, budget={state.budget_used})"
            )
        
        # Generiere Ergebnis
        result = {
            "success": True,
            "start_node": start_node_id,
            "end_node": state.current_node_id,
            "intent": intent,
            "total_moves": len(moves),
            "total_depth": state.depth,
            "budget_used": state.budget_used,
            "budget_remaining": state.budget_max - state.budget_used,
            "evidence_count": len(all_evidence),
            "total_tokens": sum(ev.get("tokens", 0) for ev in all_evidence),
            "moves": [m.to_dict() for m in moves],
            "state": state.to_dict(),
            "collected_evidence_summary": {
                "summaries": len([e for e in all_evidence if e["type"] == "summary"]),
                "code_snippets": len([e for e in all_evidence if e["type"] == "code_snippet"])
            }
        }
        
        logger.info(
            f"Exploration complete: {result['total_moves']} moves, "
            f"{result['evidence_count']} evidence items"
        )
        
        return result
    
    def resume_exploration(
        self,
        saved_state: Dict[str, Any],
        additional_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Setze vorherige Exploration fort
        
        Args:
            saved_state: Gespeicherter SnakeState als Dict
            additional_intent: Optionaler zusätzlicher Intent
            
        Returns:
            Exploration results wie explore()
        """
        state = SnakeState(
            current_node_id=saved_state["current_node_id"],
            visited_nodes=saved_state.get("visited_nodes", []),
            collected_evidence=saved_state.get("collected_evidence", []),
            path_history=saved_state.get("path_history", []),
            budget_used=saved_state.get("budget_used", 0),
            budget_max=saved_state.get("budget_max", 10000),
            intent=additional_intent or saved_state.get("intent", ""),
            depth=saved_state.get("depth", 0),
            max_depth=saved_state.get("max_depth", 5)
        )
        
        logger.info(f"Resuming exploration from {state.current_node_id}")
        
        # Continue mit explore-Logik
        return self.explore(
            start_node_id=state.current_node_id,
            intent=state.intent,
            initial_budget=state.budget_max
        )


def create_ai_snake_explorer(
    architecture_slice_service: Any,
    architecture_summary_service: Any,
    llm_client: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None
) -> AISnakeArchitectureExplorer:
    """
    Factory-Funktion zum Erstellen eines AI-Snake Explorers
    
    Args:
        architecture_slice_service: Instanz des ArchitectureSliceService
        architecture_summary_service: Instanz des ArchitectureSummaryService
        llm_client: Optionaler LLM-Client
        config: Optionale Konfiguration
        
    Returns:
        Konfigurierter AISnakeArchitectureExplorer
    """
    config = config or {}
    
    return AISnakeArchitectureExplorer(
        architecture_slice_service=architecture_slice_service,
        architecture_summary_service=architecture_summary_service,
        llm_client=llm_client,
        max_budget=config.get("max_budget", 10000),
        max_depth=config.get("max_depth", 5)
    )
