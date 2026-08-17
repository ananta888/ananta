"""
CodeCompass Layered Effective View Resolver (CIL-012)

Löst die effektive Sicht von Artefakten aus einer Layer-Kette auf.
Priorisiert neuere Layer und merged Ergebnisse deterministisch.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set
from pathlib import Path

from .layer_store import LayerStore, ArtifactLayer
from .head_registry import HeadRegistry


@dataclass
class EffectiveArtifact:
    """Ein effektiv aufgelöstes Artefakt mit Metadaten."""
    artifact_id: str
    artifact_type: str
    content_hash: str
    layer_id: str
    layer_priority: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "content_hash": self.content_hash,
            "layer_id": self.layer_id,
            "layer_priority": self.layer_priority,
            "metadata": self.metadata
        }


@dataclass
class EffectiveView:
    """Die effektive Sicht aller Artefakte für einen Head."""
    head_name: str
    head_generation: int
    layer_chain: List[str]
    artifacts: Dict[str, EffectiveArtifact]
    total_artifacts: int
    layers_scanned: int
    
    def to_dict(self) -> Dict:
        return {
            "head_name": self.head_name,
            "head_generation": self.head_generation,
            "layer_chain": self.layer_chain,
            "artifacts": {k: v.to_dict() for k, v in self.artifacts.items()},
            "total_artifacts": self.total_artifacts,
            "layers_scanned": self.layers_scanned
        }


class LayeredEffectiveViewResolver:
    """
    Löst effektive Artefakte aus einer Layer-Kette auf.
    
    Prinzipien:
    - Neuere Layer (höhere Priorität) überschreiben ältere
    - Content-addressed Deduplizierung
    - Deterministische Auflösung bei gleichen Prioritäten
    - Fail-fast bei korrupten Layern (optional skip)
    """
    
    def __init__(
        self,
        layer_store: LayerStore,
        head_registry: HeadRegistry,
        skip_corrupt: bool = True
    ):
        self.layer_store = layer_store
        self.head_registry = head_registry
        self.skip_corrupt = skip_corrupt
    
    def resolve_effective_view(
        self,
        head_name: str,
        artifact_types: Optional[List[str]] = None
    ) -> EffectiveView:
        """
        Löst die effektive Sicht für einen Head auf.
        
        Args:
            head_name: Name des Heads (z.B. "main", "feature-x")
            artifact_types: Optionale Filterung nach Artefakt-Typen
            
        Returns:
            EffectiveView mit allen aufgelösten Artefakten
        """
        # Hole Head mit Layer-Kette
        head = self.head_registry.get_head(head_name)
        if not head:
            raise ValueError(f"Head '{head_name}' nicht gefunden")
        
        layer_chain = head.layer_chain
        
        # Auflösen aller Artefakte
        artifacts: Dict[str, EffectiveArtifact] = {}
        layers_scanned = 0
        
        # Von höchster zu niedrigster Priorität (neu zuerst)
        for priority, layer_id in enumerate(reversed(layer_chain)):
            layers_scanned += 1
            
            try:
                layer = self.layer_store.read_layer(layer_id)
            except Exception as e:
                if self.skip_corrupt:
                    continue
                else:
                    raise RuntimeError(f"Korrupter Layer {layer_id}: {e}")
            
            # Artefakte hinzufügen/überschreiben
            for artifact_id, artifact_data in layer.artifacts.items():
                # Filter nach Typ falls angegeben
                if artifact_types and artifact_data.get("type") not in artifact_types:
                    continue
                
                # Nur hinzufügen wenn nicht schon von höherer Priorität
                if artifact_id not in artifacts:
                    artifacts[artifact_id] = EffectiveArtifact(
                        artifact_id=artifact_id,
                        artifact_type=artifact_data.get("type", "unknown"),
                        content_hash=artifact_data.get("content_hash", ""),
                        layer_id=layer_id,
                        layer_priority=priority,
                        metadata=artifact_data.get("metadata", {})
                    )
        
        return EffectiveView(
            head_name=head_name,
            head_generation=head.generation,
            layer_chain=layer_chain,
            artifacts=artifacts,
            total_artifacts=len(artifacts),
            layers_scanned=layers_scanned
        )
    
    def resolve_artifact(
        self,
        head_name: str,
        artifact_id: str
    ) -> Optional[EffectiveArtifact]:
        """
        Löst ein einzelnes Artefakt auf.
        
        Args:
            head_name: Name des Heads
            artifact_id: ID des Artefakts
            
        Returns:
            EffectiveArtifact oder None wenn nicht gefunden
        """
        view = self.resolve_effective_view(head_name)
        return view.artifacts.get(artifact_id)
    
    def get_artifact_content(
        self,
        head_name: str,
        artifact_id: str
    ) -> Optional[bytes]:
        """
        Holt den rohen Inhalt eines Artefakts.
        
        Args:
            head_name: Name des Heads
            artifact_id: ID des Artefakts
            
        Returns:
            Byte-Inhalt oder None
        """
        effective = self.resolve_artifact(head_name, artifact_id)
        if not effective:
            return None
        
        return self.layer_store.read_artifact_content(effective.content_hash)
    
    def list_artifact_ids(
        self,
        head_name: str,
        artifact_type: Optional[str] = None
    ) -> List[str]:
        """
        Listet alle Artefakt-IDs für einen Head.
        
        Args:
            head_name: Name des Heads
            artifact_type: Optionaler Typ-Filter
            
        Returns:
            Sortierte Liste von Artefakt-IDs
        """
        types = [artifact_type] if artifact_type else None
        view = self.resolve_effective_view(head_name, types)
        return sorted(view.artifacts.keys())
    
    def get_view_summary(self, head_name: str) -> Dict:
        """
        Bekommt eine Zusammenfassung der effektiven Sicht.
        
        Args:
            head_name: Name des Heads
            
        Returns:
            Dictionary mit Statistiken
        """
        view = self.resolve_effective_view(head_name)
        
        # Gruppiere nach Typ
        by_type: Dict[str, int] = {}
        for artifact in view.artifacts.values():
            t = artifact.artifact_type
            by_type[t] = by_type.get(t, 0) + 1
        
        return {
            "head_name": head_name,
            "generation": view.head_generation,
            "total_artifacts": view.total_artifacts,
            "layers_in_chain": len(view.layer_chain),
            "layers_scanned": view.layers_scanned,
            "by_type": by_type,
            "layer_chain": view.layer_chain
        }


def compute_effective_view_hash(view: EffectiveView) -> str:
    """
    Berechnet einen stabilen Hash für eine EffectiveView.
    
    Nützlich für Cache-Keys und Vergleich.
    """
    # Sortiere Artefakte deterministisch
    sorted_artifacts = sorted(
        view.artifacts.items(),
        key=lambda x: x[0]
    )
    
    # Baue kanonische Repräsentation
    canonical = {
        "head": view.head_name,
        "generation": view.head_generation,
        "artifacts": [
            {"id": aid, "hash": a.content_hash}
            for aid, a in sorted_artifacts
        ]
    }
    
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical_json.encode()).hexdigest()
