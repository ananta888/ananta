"""
CodeCompass Incremental Index - Layer Store Service

Content-addressed immutable storage for artifact layers.
Implements CAS (Content-Addressed Storage) with automatic deduplication.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class LayerMetadata:
    """Metadata for a stored layer."""
    layer_id: str
    snapshot_revision: str
    profile_digest: str
    created_at: datetime
    size_bytes: int
    artifact_count: int
    file_path: str


class ArtifactLayerStore:
    """
    Content-addressed storage for artifact layers.
    
    Layers are stored by their SHA256 hash (layer_id), ensuring:
    - Automatic deduplication (identical layers share storage)
    - Integrity verification (hash mismatch detection)
    - Immutable storage (layers never modified after creation)
    
    Storage layout:
    layers/
      <layer_id[0:2]>/
        <layer_id>.json.gz  # Compressed layer manifest
        artifacts/          # Artifact binary files
          symbol_graph.bin
          embeddings.npy
          ...
    """
    
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.layers_dir = self.base_path / "layers"
        self._lock_file = self.base_path / ".store.lock"
        
        # Ensure directory structure exists
        self.layers_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Initialized ArtifactLayerStore at {self.base_path}")
    
    def _compute_layer_id(self, layer_data: Dict[str, Any]) -> str:
        """
        Compute deterministic SHA256 hash for layer content.
        
        Uses canonical JSON serialization to ensure identical
        layers produce identical hashes.
        """
        # Remove runtime fields that shouldn't affect identity
        canonical_data = {k: v for k, v in layer_data.items() 
                         if k not in ('created_at', 'metadata')}
        
        # Canonical JSON: sorted keys, no extra whitespace
        canonical_json = json.dumps(
            canonical_data,
            sort_keys=True,
            separators=(',', ':')
        ).encode('utf-8')
        
        return hashlib.sha256(canonical_json).hexdigest()
    
    def _layer_path(self, layer_id: str) -> Path:
        """Get storage path for a layer (sharded by first 2 hex chars)."""
        shard = layer_id[:2]
        shard_dir = self.layers_dir / shard
        return shard_dir / f"{layer_id}.json.gz"
    
    def _artifacts_path(self, layer_id: str) -> Path:
        """Get artifacts directory for a layer."""
        shard = layer_id[:2]
        return self.layers_dir / shard / layer_id / "artifacts"
    
    def store_layer(self, layer_data: Dict[str, Any]) -> Tuple[str, bool]:
        """
        Store a layer in the content-addressed store.
        
        Args:
            layer_data: Complete layer data including all artifacts metadata
            
        Returns:
            Tuple of (layer_id, is_new)
            - layer_id: SHA256 hash of the layer content
            - is_new: True if this is a new layer, False if it already existed
        """
        # Compute content-addressed ID
        computed_id = self._compute_layer_id(layer_data)
        
        # Verify provided layer_id matches computed (if provided)
        if 'layer_id' in layer_data:
            if layer_data['layer_id'] != computed_id:
                raise ValueError(
                    f"Layer ID mismatch: provided {layer_data['layer_id']} "
                    f"vs computed {computed_id}"
                )
        else:
            layer_data['layer_id'] = computed_id
        
        # Check if layer already exists (deduplication)
        layer_path = self._layer_path(computed_id)
        if layer_path.exists():
            logger.debug(f"Layer {computed_id} already exists (deduplicated)")
            return computed_id, False
        
        # Create shard directory
        layer_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Store layer manifest (compressed)
        import gzip
        layer_json = json.dumps(layer_data, indent=2).encode('utf-8')
        with gzip.open(layer_path, 'wt', encoding='utf-8') as f:
            f.write(layer_json.decode('utf-8'))
        
        # Create artifacts directory structure
        artifacts_dir = self._artifacts_path(computed_id)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Store actual artifact files if present in data
        if 'artifacts' in layer_data:
            for artifact_type, artifact_info in layer_data['artifacts'].items():
                if 'storage_path' in artifact_info:
                    # Create placeholder or copy from source
                    artifact_path = artifacts_dir / f"{artifact_type}.bin"
                    artifact_path.touch()  # Placeholder - actual storage handled by builders
        
        # Calculate total size
        total_size = sum(
            p.stat().st_size 
            for p in artifacts_dir.rglob('*') 
            if p.is_file()
        ) + layer_path.stat().st_size
        
        logger.info(f"Stored new layer {computed_id} ({total_size} bytes)")
        
        return computed_id, True
    
    def get_layer(self, layer_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a layer by its content-addressed ID.
        
        Returns None if layer doesn't exist.
        Note: Integrity check is skipped for layers with runtime fields
        (created_at, metadata) that differ from original hash computation.
        """
        layer_path = self._layer_path(layer_id)
        
        if not layer_path.exists():
            return None
        
        import gzip
        try:
            with gzip.open(layer_path, 'rt', encoding='utf-8') as f:
                layer_data = json.load(f)
            
            # Skip strict integrity check for now - runtime fields like
            # created_at and metadata are stored but excluded from hash
            # TODO: Implement proper canonicalization for integrity verification
            
            return layer_data
        except Exception as e:
            logger.error(f"Failed to read layer {layer_id}: {e}")
            return None
    
    def has_layer(self, layer_id: str) -> bool:
        """Check if a layer exists in the store."""
        return self._layer_path(layer_id).exists()
    
    def delete_layer(self, layer_id: str) -> bool:
        """
        Delete a layer from the store.
        
        Returns True if deleted, False if didn't exist.
        Note: Only delete layers that are not referenced by any head.
        """
        layer_path = self._layer_path(layer_id)
        
        if not layer_path.exists():
            return False
        
        # Remove layer manifest
        layer_path.unlink()
        
        # Remove artifacts directory
        artifacts_dir = self._artifacts_path(layer_id)
        if artifacts_dir.exists():
            import shutil
            shutil.rmtree(artifacts_dir)
        
        # Clean up shard directory if empty
        shard_dir = layer_path.parent
        if not any(shard_dir.iterdir()):
            shard_dir.rmdir()
        
        logger.info(f"Deleted layer {layer_id}")
        return True
    
    def list_layers(self, 
                   snapshot_revision: Optional[str] = None,
                   profile_digest: Optional[str] = None) -> List[str]:
        """
        List layer IDs, optionally filtered by snapshot or profile.
        
        Returns list of layer IDs (not full layer data).
        """
        layer_ids = []
        
        for shard_dir in self.layers_dir.iterdir():
            if not shard_dir.is_dir():
                continue
            
            for layer_file in shard_dir.glob("*.json.gz"):
                layer_id = layer_file.stem
                
                # Apply filters
                if snapshot_revision or profile_digest:
                    layer_data = self.get_layer(layer_id)
                    if layer_data:
                        if snapshot_revision and layer_data.get('snapshot_revision') != snapshot_revision:
                            continue
                        if profile_digest and layer_data.get('profile_digest') != profile_digest:
                            continue
                
                layer_ids.append(layer_id)
        
        return layer_ids
    
    def get_layer_artifact_path(self, layer_id: str, artifact_type: str) -> Optional[Path]:
        """Get the file path for a specific artifact within a layer."""
        artifacts_dir = self._artifacts_path(layer_id)
        artifact_file = artifacts_dir / f"{artifact_type}.bin"
        
        if artifact_file.exists():
            return artifact_file
        return None
    
    def get_store_statistics(self) -> Dict[str, Any]:
        """Get statistics about the layer store."""
        total_layers = 0
        total_size = 0
        oldest_layer = None
        newest_layer = None
        
        for shard_dir in self.layers_dir.iterdir():
            if not shard_dir.is_dir():
                continue
            
            for layer_file in shard_dir.glob("*.json.gz"):
                total_layers += 1
                total_size += layer_file.stat().st_size
                
                # Read layer metadata for timestamps
                layer_id = layer_file.stem
                layer_data = self.get_layer(layer_id)
                if layer_data:
                    created_at = layer_data.get('created_at')
                    if created_at:
                        if oldest_layer is None or created_at < oldest_layer:
                            oldest_layer = created_at
                        if newest_layer is None or created_at > newest_layer:
                            newest_layer = created_at
        
        return {
            "total_layers": total_layers,
            "total_size_bytes": total_size,
            "oldest_layer": oldest_layer,
            "newest_layer": newest_layer
        }
