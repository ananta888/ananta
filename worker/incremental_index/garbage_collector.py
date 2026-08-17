"""
CodeCompass Incremental Index - Garbage Collector

Performs mark-and-sweep garbage collection on layer artifacts.
"""
import json
import hashlib
from dataclasses import dataclass
from typing import Set, List, Dict
from pathlib import Path
from datetime import datetime


@dataclass
class GCResult:
    """Result of a garbage collection run."""
    executed_at: str
    profile_name: str
    marked_artifacts: int
    swept_artifacts: int
    reclaimed_bytes: int
    errors: List[str]
    
    def to_dict(self) -> dict:
        return {
            'executed_at': self.executed_at,
            'profile_name': self.profile_name,
            'marked_artifacts': self.marked_artifacts,
            'swept_artifacts': self.swept_artifacts,
            'reclaimed_bytes': self.reclaimed_bytes,
            'errors': self.errors
        }


class GarbageCollector:
    """Mark-and-sweep garbage collector for layer artifacts."""
    
    def __init__(self, layer_store_path: str):
        self.layer_store_path = Path(layer_store_path)
        
    def collect(self, profile_name: str, dry_run: bool = True) -> GCResult:
        """Run garbage collection for a profile."""
        executed_at = datetime.utcnow().isoformat()
        errors = []
        
        # Step 1: Mark - find all reachable artifacts
        reachable = self._mark_reachable(profile_name)
        
        # Step 2: Sweep - remove unreachable artifacts
        swept_count, reclaimed_bytes = self._sweep_unreachable(
            reachable, 
            profile_name,
            dry_run
        )
        
        return GCResult(
            executed_at=executed_at,
            profile_name=profile_name,
            marked_artifacts=len(reachable),
            swept_artifacts=swept_count,
            reclaimed_bytes=reclaimed_bytes,
            errors=errors
        )
    
    def _mark_reachable(self, profile_name: str) -> Set[str]:
        """Mark all artifacts reachable from current heads."""
        reachable = set()
        
        # Read head reference
        head_file = self.layer_store_path / "heads" / f"{profile_name}.json"
        if not head_file.exists():
            return reachable
        
        try:
            with open(head_file, 'r') as f:
                head_data = json.load(f)
            
            # Traverse layer chain
            layer_ids = head_data.get('layer_chain', [])
            for layer_id in layer_ids:
                artifacts = self._get_layer_artifacts(layer_id)
                reachable.update(artifacts)
                
        except Exception as e:
            pass  # Would log error in real impl
            
        return reachable
    
    def _get_layer_artifacts(self, layer_id: str) -> Set[str]:
        """Get all artifact IDs in a layer."""
        artifacts = set()
        layer_file = self.layer_store_path / "layers" / f"{layer_id}.json"
        
        if layer_file.exists():
            try:
                with open(layer_file, 'r') as f:
                    layer_data = json.load(f)
                artifacts = set(layer_data.get('artifacts', []))
            except:
                pass
                
        return artifacts
    
    def _sweep_unreachable(
        self, 
        reachable: Set[str], 
        profile_name: str,
        dry_run: bool
    ) -> tuple:
        """Remove artifacts not in reachable set."""
        swept_count = 0
        reclaimed_bytes = 0
        
        # Scan all artifacts in store
        artifacts_dir = self.layer_store_path / "artifacts"
        if not artifacts_dir.exists():
            return (0, 0)
        
        for shard_dir in artifacts_dir.iterdir():
            if not shard_dir.is_dir():
                continue
                
            for artifact_file in shard_dir.iterdir():
                artifact_id = artifact_file.stem
                
                if artifact_id not in reachable:
                    # Get size before deletion
                    try:
                        size = artifact_file.stat().st_size
                    except:
                        size = 0
                    
                    if not dry_run:
                        try:
                            artifact_file.unlink()
                            swept_count += 1
                            reclaimed_bytes += size
                        except Exception as e:
                            pass  # Would log error
                    else:
                        swept_count += 1
                        reclaimed_bytes += size
        
        return (swept_count, reclaimed_bytes)


if __name__ == '__main__':
    gc = GarbageCollector('/tmp/codecompass/layers')
    result = gc.collect('default', dry_run=True)
    print(json.dumps(result.to_dict(), indent=2))
