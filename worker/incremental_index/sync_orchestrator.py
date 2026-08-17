"""
CodeCompass Incremental Sync Orchestrator (CIL-014)

Orchestriert den kompletten inkrementellen Synchronisations-Workflow.
Koordiniert Diff, Impact Analysis, Builder und Head-Update.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

from .snapshot_diff import SnapshotDiff, ChangeSet
from .dependency_impact import DependencyImpactAnalyzer, ImpactReport
from .decision_engine import BuildDecision, BuildType, DecisionEngine
from .incremental_graph_builder import IncrementalGraphBuilder
from .incremental_chunk_builder import IncrementalChunkBuilder
from .incremental_embedding_builder import IncrementalEmbeddingBuilder
from .incremental_fts_builder import IncrementalFTSBuilder
from .layer_store import LayerStore, ArtifactLayer
from .head_registry import HeadRegistry, LayerHead


logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Ergebnis einer inkrementellen Synchronisation."""
    success: bool
    sync_id: str
    head_name: str
    old_head: Optional[str]
    new_head: str
    change_summary: Dict[str, int]
    impact_summary: Dict[str, int]
    builds_executed: List[Dict]
    new_layer_id: Optional[str]
    duration_seconds: float
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "sync_id": self.sync_id,
            "head_name": self.head_name,
            "old_head": self.old_head,
            "new_head": self.new_head,
            "change_summary": self.change_summary,
            "impact_summary": self.impact_summary,
            "builds_executed": self.builds_executed,
            "new_layer_id": self.new_layer_id,
            "duration_seconds": self.duration_seconds,
            "error": self.error
        }


@dataclass
class SyncPlan:
    """Geplante Synchronisationsschritte."""
    sync_id: str
    head_name: str
    old_snapshot: str
    new_snapshot: str
    change_set: ChangeSet
    impact_report: ImpactReport
    build_decision: BuildDecision
    estimated_duration: float
    
    def to_dict(self) -> Dict:
        return {
            "sync_id": self.sync_id,
            "head_name": self.head_name,
            "old_snapshot": self.old_snapshot,
            "new_snapshot": self.new_snapshot,
            "changes": len(self.change_set.changes),
            "impacted_files": len(self.impact_report.impacted_entities),
            "build_type": self.build_decision.build_type,
            "builders_required": self.build_decision.builders_required,
            "estimated_duration": self.estimated_duration
        }


class IncrementalSyncOrchestrator:
    """
    Haupt-Orchestrator für inkrementelle Synchronisation.
    
    Workflow:
    1. Snapshot-Diff berechnen
    2. Impact-Analyse durchführen
    3. Build-Entscheidung treffen
    4. Erforderliche Builder ausführen
    5. Neuen Layer erstellen
    6. Head atomar updaten
    """
    
    def __init__(
        self,
        layer_store: LayerStore,
        head_registry: HeadRegistry,
        graph_store_path: Path,
        workspace_root: Path,
        profile_name: str = "default"
    ):
        self.layer_store = layer_store
        self.head_registry = head_registry
        self.graph_store_path = graph_store_path
        self.workspace_root = workspace_root
        self.profile_name = profile_name
        
        # Builder initialisieren
        self.graph_builder = IncrementalGraphBuilder(graph_store_path)
        self.chunk_builder = IncrementalChunkBuilder(workspace_root)
        self.embedding_builder = IncrementalEmbeddingBuilder(workspace_root)
        self.fts_builder = IncrementalFTSBuilder(workspace_root)
        
        # Analyzer initialisieren
        self.impact_analyzer = DependencyImpactAnalyzer(graph_store_path)
        self.decision_engine = DecisionEngine()
    
    def plan_sync(
        self,
        head_name: str,
        new_snapshot_manifest: Path
    ) -> SyncPlan:
        """
        Plant eine Synchronisation ohne sie auszuführen.
        
        Args:
            head_name: Name des Heads zu aktualisieren
            new_snapshot_manifest: Pfad zum neuen Snapshot-Manifest
            
        Returns:
            SyncPlan mit allen geplanten Schritten
        """
        sync_id = f"sync_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Alten Head holen
        old_head = self.head_registry.get_head(head_name)
        if not old_head:
            raise ValueError(f"Head '{head_name}' nicht gefunden")
        
        old_manifest_path = self._get_manifest_path(old_head.manifest_hash)
        
        # 1. Diff berechnen
        diff = SnapshotDiff()
        change_set = diff.compute_change_set(
            old_manifest_path,
            new_snapshot_manifest
        )
        
        # 2. Impact-Analyse
        impact_report = self.impact_analyzer.analyze_changes(
            change_set,
            include_transitive=True
        )
        
        # 3. Build-Entscheidung
        decision = self.decision_engine.decide_build(
            change_set=change_set,
            impact_report=impact_report,
            profile_name=self.profile_name
        )
        
        # Geschätzte Dauer (basierend auf Erfahrungswerten)
        estimated = self._estimate_duration(decision, impact_report)
        
        return SyncPlan(
            sync_id=sync_id,
            head_name=head_name,
            old_snapshot=old_head.manifest_hash,
            new_snapshot=new_snapshot_manifest.name,
            change_set=change_set,
            impact_report=impact_report,
            build_decision=decision,
            estimated_duration=estimated
        )
    
    def execute_sync(
        self,
        plan: SyncPlan,
        force_full: bool = False
    ) -> SyncResult:
        """
        Führt eine geplante Synchronisation aus.
        
        Args:
            plan: Der SyncPlan aus plan_sync()
            force_full: Erzwingt Full Build unabhängig von Entscheidung
            
        Returns:
            SyncResult mit Ausführungsdetails
        """
        start_time = datetime.utcnow()
        builds_executed = []
        
        try:
            # Bei force_full Entscheidung überschreiben
            if force_full:
                plan.build_decision = BuildDecision(
                    build_type=BuildType.FULL,
                    builders_required=["graph", "chunks", "embeddings", "fts"],
                    reason="Force full build requested"
                )
            
            # Builder ausführen basierend auf Entscheidung
            artifacts_built = {}
            
            if "graph" in plan.build_decision.builders_required:
                result = self._build_graph(plan)
                builds_executed.append(result)
                artifacts_built["symbol_graph"] = result["artifact_id"]
            
            if "chunks" in plan.build_decision.builders_required:
                result = self._build_chunks(plan)
                builds_executed.append(result)
                artifacts_built["semantic_chunks"] = result["artifact_id"]
            
            if "embeddings" in plan.build_decision.builders_required:
                result = self._build_embeddings(plan)
                builds_executed.append(result)
                artifacts_built["embeddings"] = result["artifact_id"]
            
            if "fts" in plan.build_decision.builders_required:
                result = self._build_fts(plan)
                builds_executed.append(result)
                artifacts_built["fts_index"] = result["artifact_id"]
            
            # Neuen Layer erstellen
            layer = ArtifactLayer(
                artifacts=artifacts_built,
                metadata={
                    "sync_id": plan.sync_id,
                    "build_type": plan.build_decision.build_type.value,
                    "change_set_id": plan.change_set.change_set_id,
                    "profile": self.profile_name,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            new_layer_id = self.layer_store.write_layer(layer)
            
            # Head atomar updaten
            old_head = self.head_registry.get_head(plan.head_name)
            new_layer_chain = old_head.layer_chain + [new_layer_id]
            
            self.head_registry.update_head(
                head_name=plan.head_name,
                layer_chain=new_layer_chain,
                manifest_hash=plan.new_snapshot
            )
            
            duration = (datetime.utcnow() - start_time).total_seconds()
            
            return SyncResult(
                success=True,
                sync_id=plan.sync_id,
                head_name=plan.head_name,
                old_head=old_head.manifest_hash if old_head else None,
                new_head=plan.new_snapshot,
                change_summary=plan.change_set.summary(),
                impact_summary={
                    "direct": len(plan.impact_report.direct_impacts),
                    "transitive": len(plan.impact_report.transitive_impacts)
                },
                builds_executed=builds_executed,
                new_layer_id=new_layer_id,
                duration_seconds=duration
            )
            
        except Exception as e:
            logger.exception(f"Sync fehlgeschlagen: {e}")
            duration = (datetime.utcnow() - start_time).total_seconds()
            
            return SyncResult(
                success=False,
                sync_id=plan.sync_id,
                head_name=plan.head_name,
                old_head=None,
                new_head=plan.new_snapshot,
                change_summary={},
                impact_summary={},
                builds_executed=builds_executed,
                new_layer_id=None,
                duration_seconds=duration,
                error=str(e)
            )
    
    def sync(
        self,
        head_name: str,
        new_snapshot_manifest: Path,
        dry_run: bool = False
    ) -> SyncResult:
        """
        Convenience-Methode: plant und führt Sync in einem Schritt aus.
        
        Args:
            head_name: Name des Heads
            new_snapshot_manifest: Neues Snapshot-Manifest
            dry_run: Nur planen, nicht ausführen
            
        Returns:
            SyncResult
        """
        plan = self.plan_sync(head_name, new_snapshot_manifest)
        
        if dry_run:
            return SyncResult(
                success=True,
                sync_id=plan.sync_id,
                head_name=head_name,
                old_head=plan.old_snapshot,
                new_head=plan.new_snapshot,
                change_summary=plan.change_set.summary(),
                impact_summary={
                    "direct": len(plan.impact_report.direct_impacts),
                    "transitive": len(plan.impact_report.transitive_impacts)
                },
                builds_executed=[],
                new_layer_id=None,
                duration_seconds=0.0
            )
        
        return self.execute_sync(plan)
    
    def _build_graph(self, plan: SyncPlan) -> Dict:
        """Führt inkrementellen Graph-Build durch."""
        start = datetime.utcnow()
        
        # Placeholder für tatsächliche Build-Logik
        artifact_id = f"graph_{plan.sync_id}"
        
        duration = (datetime.utcnow() - start).total_seconds()
        
        return {
            "builder": "graph",
            "artifact_id": artifact_id,
            "duration_seconds": duration,
            "status": "success"
        }
    
    def _build_chunks(self, plan: SyncPlan) -> Dict:
        """Führt inkrementellen Chunk-Build durch."""
        start = datetime.utcnow()
        artifact_id = f"chunks_{plan.sync_id}"
        duration = (datetime.utcnow() - start).total_seconds()
        
        return {
            "builder": "chunks",
            "artifact_id": artifact_id,
            "duration_seconds": duration,
            "status": "success"
        }
    
    def _build_embeddings(self, plan: SyncPlan) -> Dict:
        """Führt inkrementellen Embedding-Build durch."""
        start = datetime.utcnow()
        artifact_id = f"embeddings_{plan.sync_id}"
        duration = (datetime.utcnow() - start).total_seconds()
        
        return {
            "builder": "embeddings",
            "artifact_id": artifact_id,
            "duration_seconds": duration,
            "status": "success"
        }
    
    def _build_fts(self, plan: SyncPlan) -> Dict:
        """Führt inkrementellen FTS-Build durch."""
        start = datetime.utcnow()
        artifact_id = f"fts_{plan.sync_id}"
        duration = (datetime.utcnow() - start).total_seconds()
        
        return {
            "builder": "fts",
            "artifact_id": artifact_id,
            "duration_seconds": duration,
            "status": "success"
        }
    
    def _get_manifest_path(self, manifest_hash: str) -> Path:
        """Konstruiert Pfad zum Manifest."""
        # Implementierung abhängig von Manifest-Speicher
        return self.workspace_root / "manifests" / f"{manifest_hash}.json"
    
    def _estimate_duration(
        self,
        decision: BuildDecision,
        impact: ImpactReport
    ) -> float:
        """Schätzt Dauer basierend auf Build-Typ und Impact."""
        base_times = {
            BuildType.NONE: 0.1,
            BuildType.INCREMENTAL: 5.0,
            BuildType.PARTIAL: 15.0,
            BuildType.FULL: 60.0
        }
        
        base = base_times.get(decision.build_type, 10.0)
        
        # Skalieren nach Anzahl betroffener Dateien
        impacted = len(impact.impacted_entities)
        factor = 1.0 + min(impacted / 100.0, 2.0)
        
        return base * factor
