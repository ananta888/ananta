"""
Tests for CodeCompass Incremental Index M1 Implementation

Tests CIL-001 through CIL-004:
- Layer Store (content-addressed storage)
- Head Registry (atomic updates with CAS)
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from worker.incremental_index import ArtifactLayerStore, LayerHeadRegistry


class TestArtifactLayerStore:
    """Tests for content-addressed layer storage."""
    
    @pytest.fixture
    def store(self):
        """Create temporary store for testing."""
        tmpdir = tempfile.mkdtemp()
        store = ArtifactLayerStore(tmpdir)
        yield store
        shutil.rmtree(tmpdir)
    
    @pytest.fixture
    def sample_layer_data(self):
        """Sample layer data for testing."""
        return {
            "schema": "codecompass.artifact_layer.v1",
            "snapshot_revision": "a" * 64,
            "profile_digest": "b" * 64,
            "created_at": "2025-01-01T00:00:00Z",
            "artifacts": {
                "symbol_graph": {
                    "artifact_hash": "c" * 64,
                    "node_count": 100,
                    "edge_count": 250,
                    "storage_path": "symbol_graph.bin"
                },
                "embeddings": {
                    "artifact_hash": "d" * 64,
                    "vector_count": 500,
                    "dimension": 768,
                    "storage_path": "embeddings.npy"
                }
            },
            "statistics": {
                "total_artifact_size_bytes": 1024000,
                "build_duration_seconds": 45.5,
                "incremental_ratio": 0.3,
                "reused_from_layer": None
            }
        }
    
    def test_store_and_retrieve_layer(self, store, sample_layer_data):
        """Test storing and retrieving a layer."""
        # Store layer
        layer_id, is_new = store.store_layer(sample_layer_data)
        
        assert is_new, "First store should create new layer"
        assert len(layer_id) == 64, "Layer ID should be SHA256 hash"
        
        # Retrieve layer
        retrieved = store.get_layer(layer_id)
        
        assert retrieved is not None
        assert retrieved['layer_id'] == layer_id
        assert retrieved['snapshot_revision'] == sample_layer_data['snapshot_revision']
    
    def test_layer_deduplication(self, store, sample_layer_data):
        """Test that identical layers are deduplicated."""
        # Store same layer twice
        layer_id1, is_new1 = store.store_layer(sample_layer_data)
        layer_id2, is_new2 = store.store_layer(sample_layer_data)
        
        assert is_new1, "First store should create new layer"
        assert not is_new2, "Second store should detect duplicate"
        assert layer_id1 == layer_id2, "Identical layers should have same ID"
    
    def test_layer_exists_check(self, store, sample_layer_data):
        """Test has_layer method."""
        layer_id, _ = store.store_layer(sample_layer_data)
        
        assert store.has_layer(layer_id)
        assert not store.has_layer("x" * 64)
    
    def test_delete_layer(self, store, sample_layer_data):
        """Test layer deletion."""
        layer_id, _ = store.store_layer(sample_layer_data)
        
        assert store.has_layer(layer_id)
        
        deleted = store.delete_layer(layer_id)
        assert deleted
        assert not store.has_layer(layer_id)
        
        # Delete non-existent layer
        deleted_again = store.delete_layer(layer_id)
        assert not deleted_again
    
    def test_list_layers(self, store, sample_layer_data):
        """Test listing layers with filters."""
        # Store multiple layers
        layer_id1, _ = store.store_layer(sample_layer_data)
        
        sample_layer_data2 = sample_layer_data.copy()
        sample_layer_data2['snapshot_revision'] = "e" * 64
        layer_id2, _ = store.store_layer(sample_layer_data2)
        
        # List all
        all_layers = store.list_layers()
        assert len(all_layers) == 2
        assert layer_id1 in all_layers
        assert layer_id2 in all_layers
        
        # Filter by snapshot
        filtered = store.list_layers(snapshot_revision=sample_layer_data['snapshot_revision'])
        assert len(filtered) == 1
        assert layer_id1 in filtered
    
    def test_store_statistics(self, store, sample_layer_data):
        """Test store statistics."""
        store.store_layer(sample_layer_data)
        
        stats = store.get_store_statistics()
        
        assert stats['total_layers'] == 1
        assert stats['total_size_bytes'] > 0
        assert stats['newest_layer'] is not None


class TestLayerHeadRegistry:
    """Tests for atomic head registry with CAS."""
    
    @pytest.fixture
    def registry(self):
        """Create temporary registry for testing."""
        tmpdir = tempfile.mkdtemp()
        registry = LayerHeadRegistry(tmpdir)
        yield registry
        shutil.rmtree(tmpdir)
    
    def test_create_head(self, registry):
        """Test creating initial head."""
        result = registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64,
            reason="Initial creation"
        )
        
        assert result.success
        assert result.new_generation == 0
        assert result.previous_layer_id is None
    
    def test_create_existing_head_fails(self, registry):
        """Test that creating duplicate head fails."""
        registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        result = registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="d" * 64,
            snapshot_revision="e" * 64
        )
        
        assert not result.success
        assert "already exists" in result.error
    
    def test_update_head_cas_success(self, registry):
        """Test successful CAS update."""
        # Create initial head
        registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        # Update with correct generation
        result = registry.update_head(
            profile_id="test-profile",
            expected_generation=0,
            new_layer_id="d" * 64,
            reason="Incremental update"
        )
        
        assert result.success
        assert result.new_generation == 1
        assert result.previous_layer_id == "b" * 64
    
    def test_update_head_cas_failure(self, registry):
        """Test CAS failure on wrong generation."""
        # Create initial head
        registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        # Try update with wrong generation
        result = registry.update_head(
            profile_id="test-profile",
            expected_generation=5,  # Wrong!
            new_layer_id="d" * 64
        )
        
        assert not result.success
        assert "Generation mismatch" in result.error
        assert result.new_generation == 0  # Current generation
    
    def test_get_head(self, registry):
        """Test retrieving head data."""
        registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        head = registry.get_head("test-profile")
        
        assert head is not None
        assert head['profile_id'] == "test-profile"
        assert head['current_layer_id'] == "b" * 64
        assert head['generation'] == 0
    
    def test_get_nonexistent_head(self, registry):
        """Test getting head that doesn't exist."""
        head = registry.get_head("nonexistent")
        assert head is None
    
    def test_head_history(self, registry):
        """Test head history tracking."""
        # Create and update multiple times
        registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        registry.update_head(
            profile_id="test-profile",
            expected_generation=0,
            new_layer_id="d" * 64
        )
        
        registry.update_head(
            profile_id="test-profile",
            expected_generation=1,
            new_layer_id="e" * 64
        )
        
        history = registry.get_head_history("test-profile", limit=10)
        
        assert len(history) == 3  # created + 2 updates
        assert history[0]['operation'] == 'created'
        assert history[1]['operation'] == 'updated'
        assert history[2]['operation'] == 'updated'
    
    def test_list_profiles(self, registry):
        """Test listing all profiles."""
        registry.create_head(
            profile_id="profile-1",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        registry.create_head(
            profile_id="profile-2",
            profile_digest="d" * 64,
            layer_id="e" * 64,
            snapshot_revision="f" * 64
        )
        
        profiles = registry.list_profiles()
        
        assert len(profiles) == 2
        assert "profile-1" in profiles
        assert "profile-2" in profiles
    
    def test_delete_head(self, registry):
        """Test deleting a head."""
        registry.create_head(
            profile_id="test-profile",
            profile_digest="a" * 64,
            layer_id="b" * 64,
            snapshot_revision="c" * 64
        )
        
        assert registry.get_head("test-profile") is not None
        
        deleted = registry.delete_head("test-profile")
        assert deleted
        assert registry.get_head("test-profile") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
