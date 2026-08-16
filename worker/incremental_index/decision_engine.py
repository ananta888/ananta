"""
CodeCompass Incremental Index - Decision Engine

Entscheidet basierend auf ChangeSet, Impact-Analyse und Build-Profil ob ein
inkrementelles Update, teilweiser Rebuild oder vollständiger Rebuild erforderlich ist.
"""

import json
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DecisionType(Enum):
    """Mögliche Entscheidungstypen der Engine."""
    NOOP = "noop"  # Keine Aktion erforderlich
    METADATA_ONLY = "metadata_only"  # Nur Metadaten aktualisieren
    DELTA_BUILD = "delta_build"  # Inkrementelles Delta bauen
    PARTIAL_BASE_REBUILD = "partial_base_rebuild"  # Teil der Basis neu bauen
    ARTIFACT_KIND_REBASE = "artifact_kind_rebase"  # Kompletter Rebuild eines Artifact-Typs
    FULL_REBUILD = "full_rebuild"  # Vollständiger Rebuild aller Artefakte


@dataclass
class DecisionFactors:
    """Faktoren die in die Entscheidung einfliessen."""
    changeset_size: int  # Anzahl geänderter Dateien
    direct_impact_count: int  # Direkt betroffene Artefakte
    transitive_impact_count: int  # Transitiv betroffene Artefakte
    severity_score: float  # 0.0 - 1.0 aus Impact-Analyse
    compatibility_broken: bool  # Build-Profil Inkompatibilität
    embedding_model_changed: bool  # Embedding-Modell Wechsel
    fts_config_changed: bool  # FTS-Konfigurationsänderung
    graph_schema_changed: bool  # Graph-Schema Änderung
    delta_depth: int  # Anzahl un-kompaktierter Delta-Layer
    max_delta_depth: int  # Maximal erlaubte Delta-Tiefe
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "changeset_size": self.changeset_size,
            "direct_impact_count": self.direct_impact_count,
            "transitive_impact_count": self.transitive_impact_count,
            "severity_score": round(self.severity_score, 3),
            "compatibility_broken": self.compatibility_broken,
            "embedding_model_changed": self.embedding_model_changed,
            "fts_config_changed": self.fts_config_changed,
            "graph_schema_changed": self.graph_schema_changed,
            "delta_depth": self.delta_depth,
            "max_delta_depth": self.max_delta_depth
        }


@dataclass
class BuildDecision:
    """Ergebnis der Decision Engine."""
    decision_type: DecisionType
    confidence: float  # 0.0 - 1.0, wie sicher ist die Entscheidung
    reason: str  # Menschlich lesbare Begründung
    affected_artifact_kinds: List[str]  # Welche Artefakt-Typen betroffen sind
    estimated_cost: str  # "low", "medium", "high"
    dry_run_plan: List[str]  # Geplante Schritte für Dry-Run
    factors: DecisionFactors
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_type": self.decision_type.value,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "affected_artifact_kinds": sorted(self.affected_artifact_kinds),
            "estimated_cost": self.estimated_cost,
            "dry_run_plan": self.dry_run_plan,
            "factors": self.factors.to_dict()
        }


class IncrementalBuildDecisionEngine:
    """
    Entscheidet über inkrementelle Updates vs. Rebuilds basierend auf multiplen Faktoren.
    
    Entscheidungslogik:
    1. NOOP: Wenn keine Änderungen oder nur Metadaten
    2. DELTA_BUILD: Kleine kompatible Änderungen mit lokalem Impact
    3. PARTIAL_BASE_REBUILD: Größerer Impact aber innerhalb eines Artifact-Kinds
    4. ARTIFACT_KIND_REBASE: Spezifischer Artifact-Typ muss komplett neu (z.B. Embeddings bei Modellwechsel)
    5. FULL_REBUILD: Inkompatibilität, zu großer Impact, oder zu viele Delta-Layer
    """
    
    # Thresholds für Entscheidungen
    SMALL_CHANGESET_THRESHOLD = 5  # <= 5 Dateien = klein
    MEDIUM_CHANGESET_THRESHOLD = 20  # <= 20 Dateien = mittel
    LOW_SEVERITY_THRESHOLD = 0.1  # < 10% Impact = low
    MEDIUM_SEVERITY_THRESHOLD = 0.3  # < 30% Impact = medium
    HIGH_SEVERITY_THRESHOLD = 0.5  # >= 50% Impact = high
    MAX_DELTA_DEPTH_DEFAULT = 10  # Maximale Delta-Layer vor Compaction-Erzwingung
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialisiert die Decision Engine mit optionalem Config-Override.
        
        Args:
            config: Optionale Konfiguration für Thresholds
        """
        self.config = config or {}
        self.small_changeset_threshold = self.config.get(
            "small_changeset_threshold", self.SMALL_CHANGESET_THRESHOLD
        )
        self.medium_changeset_threshold = self.config.get(
            "medium_changeset_threshold", self.MEDIUM_CHANGESET_THRESHOLD
        )
        self.low_severity_threshold = self.config.get(
            "low_severity_threshold", self.LOW_SEVERITY_THRESHOLD
        )
        self.medium_severity_threshold = self.config.get(
            "medium_severity_threshold", self.MEDIUM_SEVERITY_THRESHOLD
        )
        self.high_severity_threshold = self.config.get(
            "high_severity_threshold", self.HIGH_SEVERITY_THRESHOLD
        )
        self.max_delta_depth = self.config.get(
            "max_delta_depth", self.MAX_DELTA_DEPTH_DEFAULT
        )
    
    def decide(
        self,
        changeset_size: int,
        impact_result: Optional[Dict[str, Any]],
        build_profile_old: Dict[str, Any],
        build_profile_new: Dict[str, Any],
        delta_depth: int = 0
    ) -> BuildDecision:
        """
        Trifft eine Build-Entscheidung basierend auf allen Faktoren.
        
        Args:
            changeset_size: Anzahl geänderter Dateien im ChangeSet
            impact_result: Ergebnis der Impact-Analyse (von DependencyImpactAnalyzer)
            build_profile_old: Altes Build-Profil
            build_profile_new: Neues Build-Profil
            delta_depth: Aktuelle Anzahl un-kompaktierter Delta-Layer
            
        Returns:
            BuildDecision mit Entscheidung und Begründung
        """
        # 1. Faktoren extrahieren
        direct_impact_count = 0
        transitive_impact_count = 0
        severity_score = 0.0
        
        if impact_result:
            direct_impact_count = len(impact_result.get("direct_impact", []))
            transitive_impact_count = len(impact_result.get("transitive_impact", []))
            severity_score = impact_result.get("severity_score", 0.0)
        
        # 2. Compatibility-Checks
        compatibility_broken = not self._check_compatibility(build_profile_old, build_profile_new)
        embedding_model_changed = self._check_embedding_model_change(build_profile_old, build_profile_new)
        fts_config_changed = self._check_fts_config_change(build_profile_old, build_profile_new)
        graph_schema_changed = self._check_graph_schema_change(build_profile_old, build_profile_new)
        
        # 3. Faktoren zusammenstellen
        factors = DecisionFactors(
            changeset_size=changeset_size,
            direct_impact_count=direct_impact_count,
            transitive_impact_count=transitive_impact_count,
            severity_score=severity_score,
            compatibility_broken=compatibility_broken,
            embedding_model_changed=embedding_model_changed,
            fts_config_changed=fts_config_changed,
            graph_schema_changed=graph_schema_changed,
            delta_depth=delta_depth,
            max_delta_depth=self.max_delta_depth
        )
        
        # 4. Entscheidungslogik anwenden
        decision_type, confidence, reason, affected_kinds, estimated_cost, plan = \
            self._apply_decision_logic(factors)
        
        return BuildDecision(
            decision_type=decision_type,
            confidence=confidence,
            reason=reason,
            affected_artifact_kinds=affected_kinds,
            estimated_cost=estimated_cost,
            dry_run_plan=plan,
            factors=factors
        )
    
    def _check_compatibility(
        self, 
        old_profile: Dict[str, Any], 
        new_profile: Dict[str, Any]
    ) -> bool:
        """Prüft ob Build-Profile kompatibel sind."""
        old_digest = old_profile.get("profile_digest", "")
        new_digest = new_profile.get("profile_digest", "")
        
        # Wenn Digest gleich, sind Profile kompatibel
        if old_digest == new_digest:
            return True
        
        # Compatibility-Key prüfen
        old_compat = old_profile.get("compatibility_key", {})
        new_compat = new_profile.get("compatibility_key", {})
        
        # Schema-Version muss gleich sein
        if old_compat.get("schema_version") != new_compat.get("schema_version"):
            return False
        
        # Pipeline-Version muss kompatibel sein
        old_pipeline = old_compat.get("pipeline_version", "0.0.0")
        new_pipeline = new_compat.get("pipeline_version", "0.0.0")
        
        # Major-Version muss gleich sein (semantische Versionierung)
        old_major = int(old_pipeline.split(".")[0])
        new_major = int(new_pipeline.split(".")[0])
        
        return old_major == new_major
    
    def _check_embedding_model_change(
        self,
        old_profile: Dict[str, Any],
        new_profile: Dict[str, Any]
    ) -> bool:
        """Prüft ob sich das Embedding-Modell geändert hat."""
        old_embed = old_profile.get("artifacts", {}).get("embeddings", {})
        new_embed = new_profile.get("artifacts", {}).get("embeddings", {})
        
        old_model = old_embed.get("model_name", "")
        new_model = new_embed.get("model_name", "")
        old_dim = old_embed.get("dimension", 0)
        new_dim = new_embed.get("dimension", 0)
        
        return old_model != new_model or old_dim != new_dim
    
    def _check_fts_config_change(
        self,
        old_profile: Dict[str, Any],
        new_profile: Dict[str, Any]
    ) -> bool:
        """Prüft ob sich die FTS-Konfiguration geändert hat."""
        old_fts = old_profile.get("artifacts", {}).get("fts_index", {})
        new_fts = new_profile.get("artifacts", {}).get("fts_index", {})
        
        # Tokenizer, Language, oder andere kritische Settings
        for key in ["tokenizer", "language", "stemmer"]:
            if old_fts.get(key) != new_fts.get(key):
                return True
        
        return False
    
    def _check_graph_schema_change(
        self,
        old_profile: Dict[str, Any],
        new_profile: Dict[str, Any]
    ) -> bool:
        """Prüft ob sich das Graph-Schema geändert hat."""
        old_graph = old_profile.get("artifacts", {}).get("symbol_graph", {})
        new_graph = new_profile.get("artifacts", {}).get("symbol_graph", {})
        
        old_schema = old_graph.get("schema_version", "")
        new_schema = new_graph.get("schema_version", "")
        
        return old_schema != new_schema
    
    def _apply_decision_logic(
        self,
        factors: DecisionFactors
    ) -> Tuple[DecisionType, float, str, List[str], str, List[str]]:
        """
        Wendet die Entscheidungslogik an.
        
        Returns:
            Tuple aus (decision_type, confidence, reason, affected_kinds, cost, plan)
        """
        # Fall 1: Inkompatibilität → FULL_REBUILD
        if factors.compatibility_broken:
            return (
                DecisionType.FULL_REBUILD,
                1.0,
                "Build-Profil-Inkompatibilität erfordert vollständigen Rebuild",
                ["symbol_graph", "semantic_chunks", "embeddings", "fts_index"],
                "high",
                ["invalidate_all_layers", "rebuild_base_from_scratch", "reset_heads"]
            )
        
        # Fall 2: Embedding-Modellwechsel → ARTIFACT_KIND_REBASE für Embeddings
        if factors.embedding_model_changed:
            return (
                DecisionType.ARTIFACT_KIND_REBASE,
                1.0,
                "Embedding-Modellwechsel erfordert neuen Vector-Index",
                ["embeddings"],
                "medium",
                ["preserve_other_layers", "rebuild_embeddings_base", "update_embeddings_head"]
            )
        
        # Fall 3: FTS-Konfigurationsänderung → ARTIFACT_KIND_REBASE für FTS
        if factors.fts_config_changed:
            return (
                DecisionType.ARTIFACT_KIND_REBASE,
                0.95,
                "FTS-Konfigurationsänderung erfordert neuen Suchindex",
                ["fts_index"],
                "medium",
                ["preserve_other_layers", "rebuild_fts_base", "update_fts_head"]
            )
        
        # Fall 4: Graph-Schema-Änderung → ARTIFACT_KIND_REBASE für Symbol-Graph
        if factors.graph_schema_changed:
            return (
                DecisionType.ARTIFACT_KIND_REBASE,
                0.95,
                "Graph-Schema-Änderung erfordert neuen Symbol-Graph",
                ["symbol_graph"],
                "medium",
                ["preserve_other_layers", "rebuild_symbol_graph_base", "update_graph_head"]
            )
        
        # Fall 5: Zu viele Delta-Layer → PARTIAL_BASE_REBUILD oder FULL_REBUILD
        if factors.delta_depth >= self.max_delta_depth:
            if factors.severity_score > self.high_severity_threshold:
                return (
                    DecisionType.FULL_REBUILD,
                    0.9,
                    f"Maximale Delta-Tiefe ({self.max_delta_depth}) überschritten + hoher Impact",
                    ["symbol_graph", "semantic_chunks", "embeddings", "fts_index"],
                    "high",
                    ["compact_all_layers", "rebuild_unified_base", "reset_all_heads"]
                )
            else:
                return (
                    DecisionType.PARTIAL_BASE_REBUILD,
                    0.85,
                    f"Maximale Delta-Tiefe ({self.max_delta_depth}) überschritten, Compaction empfohlen",
                    ["all"],
                    "medium",
                    ["run_compaction_planner", "merge_delta_layers", "create_new_base"]
                )
        
        # Fall 6: Kein Impact → NOOP oder METADATA_ONLY
        if factors.severity_score == 0.0 or factors.changeset_size == 0:
            if factors.changeset_size > 0:
                return (
                    DecisionType.METADATA_ONLY,
                    0.9,
                    "Nur Metadaten-Änderungen, keine Artefakt-Rebuilds erforderlich",
                    [],
                    "low",
                    ["update_manifest_metadata", "refresh_head_timestamp"]
                )
            else:
                return (
                    DecisionType.NOOP,
                    1.0,
                    "Keine Änderungen erkannt",
                    [],
                    "low",
                    ["skip_build"]
                )
        
        # Fall 7: Hoher Severity-Score → PARTIAL_BASE_REBUILD oder FULL_REBUILD
        if factors.severity_score >= self.high_severity_threshold:
            return (
                DecisionType.PARTIAL_BASE_REBUILD,
                0.8,
                f"Hoher Impact ({factors.severity_score:.1%}) erfordert Basis-Rebuild",
                ["symbol_graph", "semantic_chunks"],
                "high",
                ["identify_affected_subgraphs", "rebuild_affected_base_sections", "merge_with_unchanged"]
            )
        
        # Fall 8: Mittlerer Severity-Score → DELTA_BUILD
        if factors.severity_score >= self.medium_severity_threshold:
            return (
                DecisionType.DELTA_BUILD,
                0.85,
                f"Mittlerer Impact ({factors.severity_score:.1%}), inkrementelles Delta ausreichend",
                ["symbol_graph", "semantic_chunks"],
                "medium",
                ["build_delta_layer", "apply_incremental_updates", "update_heads"]
            )
        
        # Fall 9: Kleiner Severity-Score → DELTA_BUILD
        if factors.severity_score >= self.low_severity_threshold:
            return (
                DecisionType.DELTA_BUILD,
                0.9,
                f"Kleiner Impact ({factors.severity_score:.1%}), effizientes Delta möglich",
                ["symbol_graph"],
                "low",
                ["build_minimal_delta", "update_affected_nodes_only", "increment_delta_depth"]
            )
        
        # Default-Fall: DELTA_BUILD mit niedriger Confidence
        return (
            DecisionType.DELTA_BUILD,
            0.7,
            "Standard-Entscheidung für kleine Änderungen",
            ["symbol_graph"],
            "low",
            ["build_conservative_delta", "validate_effective_view", "update_heads"]
        )
    
    def export_decision_report(self, decision: BuildDecision, output_path: str):
        """
        Exportiert einen Decision-Report als JSON.
        
        Args:
            decision: Die getroffene Build-Entscheidung
            output_path: Pfad zur Ausgabedatei
        """
        report = {
            "report_type": "incremental_build_decision",
            "version": "1.0",
            "decision": decision.to_dict(),
            "recommendations": self._generate_recommendations(decision),
            "thresholds_used": {
                "small_changeset": self.small_changeset_threshold,
                "medium_changeset": self.medium_changeset_threshold,
                "low_severity": self.low_severity_threshold,
                "medium_severity": self.medium_severity_threshold,
                "high_severity": self.high_severity_threshold,
                "max_delta_depth": self.max_delta_depth
            }
        }
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    
    def _generate_recommendations(self, decision: BuildDecision) -> List[str]:
        """Generiert Empfehlungen basierend auf der Entscheidung."""
        recommendations = []
        
        if decision.decision_type == DecisionType.FULL_REBUILD:
            recommendations.append("Consider scheduling rebuild during off-peak hours")
            recommendations.append("Ensure sufficient storage for parallel base construction")
        elif decision.decision_type == DecisionType.ARTIFACT_KIND_REBASE:
            recommendations.append(f"Only {', '.join(decision.affected_artifact_kinds)} need rebuilding")
            recommendations.append("Other artifact layers can be preserved")
        elif decision.decision_type == DecisionType.PARTIAL_BASE_REBUILD:
            recommendations.append("Identify minimal affected subgraph before rebuild")
            recommendations.append("Consider running compaction after rebuild")
        elif decision.decision_type == DecisionType.DELTA_BUILD:
            recommendations.append("Monitor delta depth and plan compaction proactively")
            recommendations.append("Validate effective view matches full rebuild expectation")
        elif decision.decision_type == DecisionType.METADATA_ONLY:
            recommendations.append("No artifact rebuild needed, only metadata refresh")
        elif decision.decision_type == DecisionType.NOOP:
            recommendations.append("No action required, system is up-to-date")
        
        if decision.confidence < 0.8:
            recommendations.append("Low confidence: consider manual review or dry-run first")
        
        if decision.factors.delta_depth >= self.max_delta_depth * 0.8:
            recommendations.append("Delta depth approaching limit: schedule compaction soon")
        
        return recommendations


def make_build_decision(
    changeset_size: int,
    impact_result: Optional[Dict[str, Any]],
    build_profile_old: Dict[str, Any],
    build_profile_new: Dict[str, Any],
    delta_depth: int = 0,
    config: Optional[Dict[str, Any]] = None
) -> BuildDecision:
    """
    Convenience-Funktion für Build-Entscheidungen.
    
    Args:
        changeset_size: Anzahl geänderter Dateien
        impact_result: Ergebnis der Impact-Analyse
        build_profile_old: Altes Build-Profil
        build_profile_new: Neues Build-Profil
        delta_depth: Aktuelle Delta-Tiefe
        config: Optionale Konfiguration
        
    Returns:
        BuildDecision mit Empfehlung
    """
    engine = IncrementalBuildDecisionEngine(config)
    return engine.decide(
        changeset_size=changeset_size,
        impact_result=impact_result,
        build_profile_old=build_profile_old,
        build_profile_new=build_profile_new,
        delta_depth=delta_depth
    )


if __name__ == "__main__":
    # Beispiel-Nutzung
    old_profile = {
        "profile_digest": "abc123",
        "compatibility_key": {"schema_version": "1.0", "pipeline_version": "2.1.0"},
        "artifacts": {
            "embeddings": {"model_name": "sentence-transformers/all-MiniLM-L6-v2", "dimension": 384},
            "fts_index": {"tokenizer": "standard", "language": "en"},
            "symbol_graph": {"schema_version": "1.0"}
        }
    }
    
    new_profile = {
        "profile_digest": "def456",
        "compatibility_key": {"schema_version": "1.0", "pipeline_version": "2.1.1"},
        "artifacts": {
            "embeddings": {"model_name": "sentence-transformers/all-MiniLM-L6-v2", "dimension": 384},
            "fts_index": {"tokenizer": "standard", "language": "en"},
            "symbol_graph": {"schema_version": "1.0"}
        }
    }
    
    impact_result = {
        "direct_impact": ["node1", "node2"],
        "transitive_impact": ["node3"],
        "severity_score": 0.15
    }
    
    decision = make_build_decision(
        changeset_size=3,
        impact_result=impact_result,
        build_profile_old=old_profile,
        build_profile_new=new_profile,
        delta_depth=2
    )
    
    print(f"Decision: {json.dumps(decision.to_dict(), indent=2)}")
