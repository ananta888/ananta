"""
CodeCompass RLM Eignungsentscheidung und Fallback

Deterministische Heuristiken zur Entscheidung wann RLM (Recursive Language Model)
einen Mehrwert bietet und wann der normale Context Planner ausreicht.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional, Set
import re


class RLMDecision(Enum):
    """Entscheidung über RLM-Nutzung"""
    USE_RLM = "use_rlm"
    USE_NORMAL_PLANNER = "use_normal_planner"
    UNCERTAIN_FALLBACK = "uncertain_fallback"


@dataclass
class RLMEvaluationResult:
    """Ergebnis der RLM-Eignungsprüfung"""
    decision: RLMDecision
    reasons: List[str]
    confidence_score: float  # 0.0 - 1.0
    suggested_mode: str
    complexity_score: int  # 0-100
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": self.reasons,
            "confidence_score": self.confidence_score,
            "suggested_mode": self.suggested_mode,
            "complexity_score": self.complexity_score
        }


class RLMSuitabilityEvaluator:
    """
    Bewertet ob eine Query von rekursiver Analyse profitiert.
    
    Heuristiken basierend auf:
    - Query-Komplexität und Länge
    - Anzahl der beteiligten Module/Sprachen
    - Intent-Typ
    - Erwarteter Kontextumfang
    - Dependency-Graph-Anforderungen
    """
    
    # Komplexe Intents die RLM profitieren können
    RLM_SUITABLE_INTENTS = {
        "architecture_overview",
        "dependency_trace",
        "refactor_impact",
        "bug_investigation",
        "feature_location"
    }
    
    # Einfache Intents die normalerweise kein RLM benötigen
    SIMPLE_INTENTS = {
        "symbol_lookup",
        "code_explanation",
        "api_usage"
    }
    
    # Keywords die auf komplexe Queries hinweisen
    COMPLEX_QUERY_KEYWORDS = [
        "how.*work", "relationship", "dependency", "impact",
        "affect", "influence", "connect", "interact",
        "across", "throughout", "entire", "all",
        "trace", "follow", "chain", "flow"
    ]
    
    # Patterns für Multi-Module-Fragen
    MULTI_MODULE_PATTERNS = [
        r"\b(modules?|components?|services?|packages?)\b.*\b(across|between|multiple)\b",
        r"\b(integrate|integration|interface|api)\b.*\b(and|with|to)\b",
        r"\b(dependencies?|imports?|uses?)\b.*\b(\w+\s*[,;]\s*\w+)\b"
    ]
    
    def __init__(
        self,
        enable_rlm: bool = True,
        min_complexity_score: int = 40,
        max_fan_out_for_simple: int = 3
    ):
        self.enable_rlm = enable_rlm
        self.min_complexity_score = min_complexity_score
        self.max_fan_out_for_simple = max_fan_out_for_simple
    
    def evaluate(
        self,
        query: str,
        intent: str,
        source_refs: Optional[List[str]] = None,
        estimated_context_size: int = 0,
        num_languages: int = 1,
        num_modules: int = 1,
        has_dependency_graph: bool = False
    ) -> RLMEvaluationResult:
        """
        Bewertet ob RLM für diese Query geeignet ist.
        
        Args:
            query: Die User-Query
            intent: Typisierter Intent
            source_refs: Liste der Source-Referenzen/Pfade
            estimated_context_size: Geschätzte Token-Anzahl des Kontexts
            num_languages: Anzahl der beteiligten Programmiersprachen
            num_modules: Anzahl der betroffenen Module
            has_dependency_graph: Ob ein Dependency-Graph verfügbar ist
        
        Returns:
            RLMEvaluationResult mit Entscheidung und Begründung
        """
        if not self.enable_rlm:
            return RLMEvaluationResult(
                decision=RLMDecision.USE_NORMAL_PLANNER,
                reasons=["RLM ist deaktiviert"],
                confidence_score=1.0,
                suggested_mode="normal",
                complexity_score=0
            )
        
        reasons = []
        complexity_score = 0
        
        # 1. Intent-basierte Bewertung
        if intent in self.RLM_SUITABLE_INTENTS:
            complexity_score += 20
            reasons.append(f"Intent '{intent}' profitiert von rekursiver Analyse")
        elif intent in self.SIMPLE_INTENTS:
            complexity_score -= 15
            reasons.append(f"Intent '{intent}' ist typischerweise einfach")
        
        # 2. Query-Länge und Komplexität
        word_count = len(query.split())
        if word_count > 15:
            complexity_score += min(15, (word_count - 15))
            reasons.append(f"Lange Query ({word_count} Wörter) deutet auf Komplexität hin")
        
        # 3. Keywords erkennen
        query_lower = query.lower()
        complex_keywords_found = sum(
            1 for pattern in self.COMPLEX_QUERY_KEYWORDS 
            if re.search(pattern, query_lower)
        )
        if complex_keywords_found >= 2:
            complexity_score += min(20, complex_keywords_found * 7)
            reasons.append(f"{complex_keywords_found} Komplexitäts-Keywords erkannt")
        
        # 4. Multi-Module Patterns
        multi_module_matches = sum(
            1 for pattern in self.MULTI_MODULE_PATTERNS
            if re.search(pattern, query_lower)
        )
        if multi_module_matches > 0:
            complexity_score += min(15, multi_module_matches * 8)
            reasons.append(f"Multi-Module-Muster erkannt ({multi_module_matches})")
        
        # 5. Anzahl der Sprachen
        if num_languages > 1:
            complexity_score += min(15, (num_languages - 1) * 8)
            reasons.append(f"Mehrere Sprachen beteiligt ({num_languages})")
        
        # 6. Anzahl der Module
        if num_modules > 3:
            complexity_score += min(15, (num_modules - 3) * 5)
            reasons.append(f"Viele Module betroffen ({num_modules})")
        
        # 7. Source-Refs analysieren
        if source_refs:
            unique_dirs = len(set(ref.split('/')[0] for ref in source_refs if '/' in ref))
            if unique_dirs > 2:
                complexity_score += min(10, (unique_dirs - 2) * 4)
                reasons.append(f"Query spannt {unique_dirs} Verzeichnisse")
        
        # 8. Estimated Context Size
        if estimated_context_size > 8000:
            complexity_score += min(15, (estimated_context_size - 8000) // 1000)
            reasons.append(f"Großer erwarteter Kontext ({estimated_context_size} Tokens)")
        
        # 9. Dependency Graph Verfügbarkeit
        if has_dependency_graph and intent == "dependency_trace":
            complexity_score += 10
            reasons.append("Dependency-Trace mit Graph-Unterstützung")
        
        # Normalisierung
        complexity_score = max(0, min(100, complexity_score))
        
        # Entscheidung treffen
        if complexity_score >= self.min_complexity_score:
            decision = RLMDecision.USE_RLM
            confidence = min(0.95, 0.5 + (complexity_score - self.min_complexity_score) / 100)
            reasons.append(f"Komplexitäts-Score ({complexity_score}) über Schwellwert")
        elif complexity_score < 20:
            decision = RLMDecision.USE_NORMAL_PLANNER
            confidence = 0.8 + (20 - complexity_score) / 100
            reasons.append(f"Komplexitäts-Score ({complexity_score}) zu niedrig für RLM")
        else:
            decision = RLMDecision.UNCERTAIN_FALLBACK
            confidence = 0.5 + abs(complexity_score - 30) / 200
            reasons.append("Uneindeutige Signale, Fallback zu normalem Planner")
        
        # Suggested Mode
        if decision == RLMDecision.USE_RLM:
            suggested_mode = "recursive"
            fan_out = min(10, 3 + complexity_score // 15)
        elif decision == RLMDecision.USE_NORMAL_PLANNER:
            suggested_mode = "normal"
            fan_out = self.max_fan_out_for_simple
        else:
            suggested_mode = "normal"  # Safe fallback
            fan_out = self.max_fan_out_for_simple
        
        return RLMEvaluationResult(
            decision=decision,
            reasons=reasons,
            confidence_score=round(confidence, 2),
            suggested_mode=suggested_mode,
            complexity_score=complexity_score
        )
    
    def should_use_rlm(self, evaluation_result: RLMEvaluationResult) -> bool:
        """
        Einfache Boolean-Abfrage ob RLM verwendet werden soll.
        
        Args:
            evaluation_result: Vorheriges Evaluation-Ergebnis
        
        Returns:
            True wenn RLM verwendet werden soll
        """
        return evaluation_result.decision == RLMDecision.USE_RLM


def integrate_with_context_planner(planner_service):
    """
    Integriert RLM-Suitability-Evaluator in bestehenden Context Planner.
    
    Diese Funktion erweitert den CodeCompassContextPlannerService um die Fähigkeit,
    automatisch zu entscheiden ob RLM verwendet werden soll.
    """
    evaluator = RLMSuitabilityEvaluator(
        enable_rlm=getattr(planner_service, 'rlm_enabled', True),
        min_complexity_score=40,
        max_fan_out_for_simple=3
    )
    
    return evaluator


if __name__ == "__main__":
    # Demo/Test
    evaluator = RLMSuitabilityEvaluator(enable_rlm=True)
    
    # Test 1: Einfache Query
    result1 = evaluator.evaluate(
        query="What does the authenticate method do?",
        intent="symbol_lookup",
        source_refs=["src/auth/authenticator.py"],
        num_languages=1,
        num_modules=1
    )
    print("Test 1 - Einfache Query:")
    print(f"  Decision: {result1.decision.value}")
    print(f"  Complexity: {result1.complexity_score}")
    print(f"  Reasons: {result1.reasons[:2]}")
    print()
    
    # Test 2: Komplexe Query
    result2 = evaluator.evaluate(
        query="How does authentication work across all modules and what is the impact on the API layer?",
        intent="architecture_overview",
        source_refs=[
            "src/auth/authenticator.py",
            "src/api/handlers.py",
            "src/services/user_service.py",
            "src/middleware/auth_middleware.py"
        ],
        num_languages=1,
        num_modules=5,
        estimated_context_size=12000
    )
    print("Test 2 - Komplexe Query:")
    print(f"  Decision: {result2.decision.value}")
    print(f"  Complexity: {result2.complexity_score}")
    print(f"  Reasons: {result2.reasons[:3]}")
    print()
    
    # Test 3: Multi-Language Query
    result3 = evaluator.evaluate(
        query="Trace the dependency chain between Python backend and TypeScript frontend components",
        intent="dependency_trace",
        source_refs=[
            "backend/api/routes.py",
            "frontend/src/components/*.tsx",
            "shared/types.ts"
        ],
        num_languages=2,
        num_modules=4,
        has_dependency_graph=True
    )
    print("Test 3 - Multi-Language Dependency Trace:")
    print(f"  Decision: {result3.decision.value}")
    print(f"  Complexity: {result3.complexity_score}")
    print(f"  Reasons: {result3.reasons[:3]}")
    print()
    
    # Zusammenfassung
    print("Zusammenfassung:")
    print(f"  Test 1 sollte RLM nutzen: {evaluator.should_use_rlm(result1)}")
    print(f"  Test 2 sollte RLM nutzen: {evaluator.should_use_rlm(result2)}")
    print(f"  Test 3 sollte RLM nutzen: {evaluator.should_use_rlm(result3)}")
