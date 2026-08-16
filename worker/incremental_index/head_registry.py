"""
CodeCompass Incremental Index - Head Registry Service

Atomic, generation-tracked pointers to effective layers.
Implements optimistic locking via CAS (Compare-And-Swap) updates.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging
import fcntl

logger = logging.getLogger(__name__)


@dataclass
class HeadUpdateResult:
    """Result of a head update operation."""
    success: bool
    new_generation: int
    previous_layer_id: Optional[str]
    error: Optional[str] = None


class LayerHeadRegistry:
    """
    Atomic registry for layer heads with generation tracking.
    
    Each profile has exactly one head pointing to the current effective layer.
    Updates use optimistic locking via generation numbers to prevent races.
    
    Storage layout:
    heads/
      <profile_id>.json  # Current head state with history
    """
    
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.heads_dir = self.base_path / "heads"
        self._locks: Dict[str, any] = {}
        
        # Ensure directory exists
        self.heads_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Initialized LayerHeadRegistry at {self.base_path}")
    
    def _head_path(self, profile_id: str) -> Path:
        """Get storage path for a profile's head."""
        return self.heads_dir / f"{profile_id}.json"
    
    def _acquire_lock(self, profile_id: str) -> bool:
        """Acquire exclusive lock for a profile."""
        lock_path = self.base_path / f".head.{profile_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        
        lock_file = open(lock_path, 'w')
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locks[profile_id] = lock_file
            return True
        except IOError:
            lock_file.close()
            return False
    
    def _release_lock(self, profile_id: str):
        """Release lock for a profile."""
        if profile_id in self._locks:
            fcntl.flock(self._locks[profile_id].fileno(), fcntl.LOCK_UN)
            self._locks[profile_id].close()
            del self._locks[profile_id]
    
    def get_head(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """
        Get current head for a profile.
        
        Returns None if profile has no head yet.
        """
        head_path = self._head_path(profile_id)
        
        if not head_path.exists():
            return None
        
        try:
            with open(head_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read head for {profile_id}: {e}")
            return None
    
    def create_head(self, 
                   profile_id: str,
                   profile_digest: str,
                   layer_id: str,
                   snapshot_revision: str,
                   reason: Optional[str] = None) -> HeadUpdateResult:
        """
        Create initial head for a profile.
        
        Fails if head already exists (use update_head instead).
        """
        if not self._acquire_lock(profile_id):
            return HeadUpdateResult(
                success=False,
                new_generation=0,
                previous_layer_id=None,
                error="Could not acquire lock"
            )
        
        try:
            head_path = self._head_path(profile_id)
            
            if head_path.exists():
                return HeadUpdateResult(
                    success=False,
                    new_generation=0,
                    previous_layer_id=None,
                    error="Head already exists"
                )
            
            now = datetime.utcnow().isoformat() + "Z"
            
            head_data = {
                "schema": "codecompass.layer_head.v1",
                "profile_id": profile_id,
                "profile_digest": profile_digest,
                "current_layer_id": layer_id,
                "generation": 0,
                "updated_at": now,
                "snapshot_revision": snapshot_revision,
                "history": [{
                    "layer_id": layer_id,
                    "generation": 0,
                    "timestamp": now,
                    "operation": "created",
                    "previous_layer_id": None,
                    "reason": reason or "Initial head creation"
                }],
                "metadata": {}
            }
            
            with open(head_path, 'w', encoding='utf-8') as f:
                json.dump(head_data, f, indent=2)
            
            logger.info(f"Created head for {profile_id} -> {layer_id}")
            
            return HeadUpdateResult(
                success=True,
                new_generation=0,
                previous_layer_id=None
            )
        finally:
            self._release_lock(profile_id)
    
    def update_head(self,
                   profile_id: str,
                   expected_generation: int,
                   new_layer_id: str,
                   profile_digest: Optional[str] = None,
                   snapshot_revision: Optional[str] = None,
                   reason: Optional[str] = None) -> HeadUpdateResult:
        """
        Atomically update head to point to new layer.
        
        Uses optimistic locking: update fails if current generation
        doesn't match expected_generation.
        
        Args:
            profile_id: Profile identifier
            expected_generation: Expected current generation (for CAS)
            new_layer_id: New layer to point to
            profile_digest: Updated profile digest (optional)
            snapshot_revision: Snapshot revision (optional)
            reason: Human-readable reason for update
            
        Returns:
            HeadUpdateResult with success status and new generation
        """
        if not self._acquire_lock(profile_id):
            return HeadUpdateResult(
                success=False,
                new_generation=0,
                previous_layer_id=None,
                error="Could not acquire lock"
            )
        
        try:
            head_path = self._head_path(profile_id)
            
            if not head_path.exists():
                return HeadUpdateResult(
                    success=False,
                    new_generation=0,
                    previous_layer_id=None,
                    error="Head does not exist"
                )
            
            with open(head_path, 'r', encoding='utf-8') as f:
                head_data = json.load(f)
            
            # Check generation (optimistic locking)
            current_generation = head_data.get('generation', 0)
            if current_generation != expected_generation:
                return HeadUpdateResult(
                    success=False,
                    new_generation=current_generation,
                    previous_layer_id=head_data.get('current_layer_id'),
                    error=f"Generation mismatch: expected {expected_generation}, current {current_generation}"
                )
            
            # Prepare update
            new_generation = current_generation + 1
            now = datetime.utcnow().isoformat() + "Z"
            previous_layer_id = head_data.get('current_layer_id')
            
            # Update fields
            head_data['current_layer_id'] = new_layer_id
            head_data['generation'] = new_generation
            head_data['updated_at'] = now
            
            if profile_digest:
                head_data['profile_digest'] = profile_digest
            
            if snapshot_revision:
                head_data['snapshot_revision'] = snapshot_revision
            
            # Add to history (keep last 50)
            history_entry = {
                "layer_id": new_layer_id,
                "generation": new_generation,
                "timestamp": now,
                "operation": "updated",
                "previous_layer_id": previous_layer_id,
                "reason": reason
            }
            
            head_data['history'].append(history_entry)
            head_data['history'] = head_data['history'][-50:]
            
            # Write atomically (write to temp, then rename)
            temp_path = head_path.with_suffix('.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(head_data, f, indent=2)
            
            os.replace(temp_path, head_path)
            
            logger.info(
                f"Updated head for {profile_id}: gen {current_generation} -> {new_generation}, "
                f"layer {previous_layer_id[:8]}... -> {new_layer_id[:8]}..."
            )
            
            return HeadUpdateResult(
                success=True,
                new_generation=new_generation,
                previous_layer_id=previous_layer_id
            )
        finally:
            self._release_lock(profile_id)
    
    def get_head_history(self, profile_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent history for a profile's head."""
        head_data = self.get_head(profile_id)
        if not head_data:
            return []
        
        history = head_data.get('history', [])
        return history[-limit:]
    
    def delete_head(self, profile_id: str) -> bool:
        """
        Delete a profile's head.
        
        Use with caution - only for cleanup or testing.
        """
        if not self._acquire_lock(profile_id):
            return False
        
        try:
            head_path = self._head_path(profile_id)
            
            if not head_path.exists():
                return False
            
            head_path.unlink()
            logger.info(f"Deleted head for {profile_id}")
            return True
        finally:
            self._release_lock(profile_id)
    
    def list_profiles(self) -> List[str]:
        """List all profile IDs with registered heads."""
        profiles = []
        for head_file in self.heads_dir.glob("*.json"):
            profiles.append(head_file.stem)
        return profiles
    
    def get_all_heads(self) -> Dict[str, Dict[str, Any]]:
        """Get all heads as a dictionary keyed by profile_id."""
        heads = {}
        for profile_id in self.list_profiles():
            head_data = self.get_head(profile_id)
            if head_data:
                heads[profile_id] = head_data
        return heads
