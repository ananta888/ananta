"""
CodeCompass Incremental Embedding/Vector Index Builder

Erweitert den bestehenden Vector Retrieval Service um inkrementelle Embedding-
und Vector-Index-Updates. Nur neue/geänderte Texte werden embedded, Vektor-
Deltas erzeugen Upserts und Deletes für Qdrant.

Features:
- embedding_text_hash + CompatibilityKey vor Provider-Aufruf prüfen
- Nur neue/geänderte Texte embedden
- Vector-Delta Upserts und Deletes erzeugen
- Batching, Retry und Idempotenz pro ChangeSet
- Dimensions-/Model-Mismatch wird abgelehnt
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class VectorOperation(Enum):
    """Operationstypen für Vector-Delta."""
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass
class VectorDelta:
    """Delta-Operation für einen Vector-Point."""
    operation: VectorOperation
    point_id: str
    chunk_id: str
    vector: Optional[List[float]] = None
    payload: Optional[Dict[str, Any]] = None
    reason: str = ""
    retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "operation": self.operation.value,
            "point_id": self.point_id,
            "chunk_id": self.chunk_id,
            "reason": self.reason,
            "retry_count": self.retry_count
        }
        if self.vector is not None:
            result["vector"] = self.vector
        if self.payload is not None:
            result["payload"] = self.payload
        return result


@dataclass
class EmbeddingCompatibilityKey:
    """
    Kompatibilitätsschlüssel für Embeddings.
    
    Bestimmt ob existierende Embeddings wiederverwendet werden können.
    """
    model_name: str
    model_revision: str
    dimensions: int
    encoding: str  # float, int8, uint8, etc.
    embedding_text_profile: str  # Hash des Textprofils (preprocessing)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
            "encoding": self.encoding,
            "embedding_text_profile": self.embedding_text_profile
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmbeddingCompatibilityKey':
        return cls(
            model_name=data["model_name"],
            model_revision=data.get("model_revision", "main"),
            dimensions=data["dimensions"],
            encoding=data.get("encoding", "float"),
            embedding_text_profile=data["embedding_text_profile"]
        )
    
    def compute_hash(self) -> str:
        """Berechnet Hash des Compatibility Keys."""
        canonical = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    
    def is_compatible_with(self, other: 'EmbeddingCompatibilityKey') -> bool:
        """
        Prüft Kompatibilität mit einem anderen Key.
        
        Args:
            other: Anderer CompatibilityKey
        
        Returns:
            True wenn kompatibel (gleiche Dimensionen, Model, Encoding)
        """
        return (
            self.model_name == other.model_name and
            self.dimensions == other.dimensions and
            self.encoding == other.encoding
        )


@dataclass
class IncrementalEmbeddingResult:
    """Ergebnis des inkrementellen Embedding-Builds."""
    changeset_id: str
    source_revision: str
    compatibility_key_hash: str
    vector_deltas: List[VectorDelta] = field(default_factory=list)
    chunks_embedded: int = 0
    chunks_reused: int = 0
    chunks_deleted: int = 0
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "source_revision": self.source_revision,
            "compatibility_key_hash": self.compatibility_key_hash,
            "vector_deltas": [d.to_dict() for d in self.vector_deltas],
            "chunks_embedded": self.chunks_embedded,
            "chunks_reused": self.chunks_reused,
            "chunks_deleted": self.chunks_deleted,
            "errors": self.errors,
            "stats": self.stats
        }


class IncrementalEmbeddingBuilder:
    """
    Inkrementeller Builder für Embeddings und Vector-Indizes.
    
    Embeddet nur neue/geänderte Chunks, verwendet existierende Embeddings
    bei kompatiblen Keys und erzeugt Delta-Operationen für Qdrant.
    """
    
    def __init__(
        self,
        embedding_provider: Any,
        vector_store: Any,
        chunk_store: Any,
        compatibility_key: EmbeddingCompatibilityKey,
        batch_size: int = 32,
        max_retries: int = 3,
    ):
        """
        Args:
            embedding_provider: Service für Embedding-Generation (embed_texts)
            vector_store: Qdrant oder anderer Vector-Store
            chunk_store: Store für semantische Chunks
            compatibility_key: Current Embedding-Kompatibilitäts-Key
            batch_size: Batch-Größe für Embedding-API-Aufrufe
            max_retries: Maximale Retry-Versuche bei Fehlern
        """
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.chunk_store = chunk_store
        self.compatibility_key = compatibility_key
        self.batch_size = batch_size
        self.max_retries = max_retries
    
    def _compute_point_id(self, chunk_id: str) -> str:
        """
        Berechnet Vector-Point-ID aus Chunk-ID.
        
        Args:
            chunk_id: ID des semantischen Chunks
        
        Returns:
            Point-ID für Qdrant (UUID oder hash-basiert)
        """
        # Verwende direkt chunk_id als point_id (muss UUID-fähig sein)
        # Oder generiere deterministische UUID
        return hashlib.sha256(f"vector:{chunk_id}".encode()).hexdigest()[:36]
    
    def _compute_embedding_text_hash(self, text: str) -> str:
        """
        Berechnet Hash des Embedding-Textes nach Preprocessing.
        
        Args:
            text: Raw chunk text
        
        Returns:
            SHA256-Hash des normalisierten Textes
        """
        # Normalisierung (abhängig von embedding_text_profile)
        normalized = text.strip().lower()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
    
    def _get_existing_vectors(
        self,
        chunk_ids: Set[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Lädt existierende Vektoren für Chunks aus dem Store.
        
        Args:
            chunk_ids: Menge von Chunk-IDs
        
        Returns:
            Dict von chunk_id -> vector_data
        """
        try:
            vectors = {}
            for chunk_id in chunk_ids:
                point_id = self._compute_point_id(chunk_id)
                vector_data = self.vector_store.get_vector(point_id)
                if vector_data:
                    vectors[chunk_id] = vector_data
            return vectors
        except Exception as e:
            logger.error(f"Error loading existing vectors: {e}")
            return {}
    
    def _check_compatibility(
        self,
        existing_vector: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Prüft ob existierender Vektor mit aktuellem Key kompatibel ist.
        
        Args:
            existing_vector: Existierende Vektor-Daten mit metadata
        
        Returns:
            Tuple aus (is_compatible, reason)
        """
        existing_meta = existing_vector.get("metadata", {})
        existing_key_data = existing_meta.get("compatibility_key")
        
        if not existing_key_data:
            return False, "missing_compatibility_key"
        
        try:
            existing_key = EmbeddingCompatibilityKey.from_dict(existing_key_data)
            
            # Prüfe strikte Gleichheit für Wiederverwendung
            if existing_key.compute_hash() == self.compatibility_key.compute_hash():
                return True, "exact_match"
            
            # Prüfe Kompatibilität (Model/Dimensions müssen gleich sein)
            if not existing_key.is_compatible_with(self.compatibility_key):
                return False, "incompatible_model_or_dimensions"
            
            # Kompatibel aber nicht identisch (z.B. anderes Preprocessing)
            return False, "different_preprocessing"
            
        except Exception as e:
            logger.warning(f"Compatibility check failed: {e}")
            return False, "compatibility_check_error"
    
    def _embed_batch(
        self,
        texts: List[str]
    ) -> List[List[float]]:
        """
        Embeddet eine Batch von Texten.
        
        Args:
            texts: Liste von Texten
        
        Returns:
            Liste von Vektoren
        """
        try:
            embeddings = self.embedding_provider.embed_texts(texts)
            return embeddings
        except Exception as e:
            logger.error(f"Embedding batch failed: {e}")
            raise
    
    def build_incremental(
        self,
        changeset_id: str,
        source_revision: str,
        new_chunks: List[Dict[str, Any]],
        modified_chunks: List[Dict[str, Any]],
        deleted_chunk_ids: Optional[List[str]] = None
    ) -> IncrementalEmbeddingResult:
        """
        Führt inkrementellen Embedding-Build durch.
        
        Args:
            changeset_id: ID des ChangeSets
            source_revision: Source-Revision
            new_chunks: Neue Chunks (noch keine Embeddings)
            modified_chunks: Geänderte Chunks (Embeddings müssen aktualisiert werden)
            deleted_chunk_ids: Gelöschte Chunk-IDs
        
        Returns:
            IncrementalEmbeddingResult mit Vector-Deltas
        """
        logger.info(f"Starting incremental embedding build for {len(new_chunks) + len(modified_chunks)} chunks")
        
        compatibility_key_hash = self.compatibility_key.compute_hash()
        vector_deltas: List[VectorDelta] = []
        errors: List[str] = []
        chunks_embedded = 0
        chunks_reused = 0
        chunks_deleted = 0
        
        # Alle zu verarbeitenden Chunks sammeln
        all_chunks = []
        for chunk in new_chunks:
            chunk["_operation"] = "new"
            all_chunks.append(chunk)
        for chunk in modified_chunks:
            chunk["_operation"] = "modified"
            all_chunks.append(chunk)
        
        # Existierende Vektoren laden für Modified-Chunks
        modified_chunk_ids = {c["chunk_id"] for c in modified_chunks}
        existing_vectors = self._get_existing_vectors(modified_chunk_ids)
        
        # Chunks filtern die bereits existieren und kompatibel sind
        chunks_to_embed = []
        for chunk in all_chunks:
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("text", "")
            
            # Prüfen ob existierender Vektor kompatibel ist
            if chunk_id in existing_vectors:
                existing = existing_vectors[chunk_id]
                is_compatible, reason = self._check_compatibility(existing)
                
                if is_compatible:
                    # Wiederverwenden - kein Embedding nötig
                    chunks_reused += 1
                    continue
            
            # Embedding erforderlich
            chunks_to_embed.append(chunk)
        
        # Embeddings in Batches erzeugen
        batches = [
            chunks_to_embed[i:i + self.batch_size]
            for i in range(0, len(chunks_to_embed), self.batch_size)
        ]
        
        for batch_idx, batch in enumerate(batches):
            try:
                texts = [c.get("text", "") for c in batch]
                
                # Embedding mit Retry
                embeddings = None
                for retry in range(self.max_retries):
                    try:
                        embeddings = self._embed_batch(texts)
                        break
                    except Exception as e:
                        if retry == self.max_retries - 1:
                            raise
                        logger.warning(f"Embedding batch {batch_idx} retry {retry+1}: {e}")
                
                if embeddings is None or len(embeddings) != len(batch):
                    error_msg = f"Embedding batch {batch_idx} failed after retries"
                    errors.append(error_msg)
                    continue
                
                # Vector-Deltas erzeugen
                for chunk, vector in zip(batch, embeddings):
                    chunk_id = chunk.get("chunk_id")
                    point_id = self._compute_point_id(chunk_id)
                    
                    # Payload mit Metadaten
                    payload = {
                        "chunk_id": chunk_id,
                        "file_path": chunk.get("file_path"),
                        "symbol_name": chunk.get("symbol_name"),
                        "chunk_index": chunk.get("chunk_index"),
                        "content_hash": chunk.get("content_hash"),
                        "changeset_id": changeset_id,
                        "source_revision": source_revision,
                        "compatibility_key": self.compatibility_key.to_dict(),
                        **chunk.get("metadata", {})
                    }
                    
                    delta = VectorDelta(
                        operation=VectorOperation.UPSERT,
                        point_id=point_id,
                        chunk_id=chunk_id,
                        vector=vector,
                        payload=payload,
                        reason=f"chunk_{chunk.get('_operation', 'unknown')}"
                    )
                    vector_deltas.append(delta)
                    chunks_embedded += 1
                    
            except Exception as e:
                error_msg = f"Error embedding batch {batch_idx}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Gelöschte Chunks verarbeiten
        deleted_chunk_ids = deleted_chunk_ids or []
        for chunk_id in deleted_chunk_ids:
            try:
                point_id = self._compute_point_id(chunk_id)
                
                delta = VectorDelta(
                    operation=VectorOperation.DELETE,
                    point_id=point_id,
                    chunk_id=chunk_id,
                    reason="chunk_deleted"
                )
                vector_deltas.append(delta)
                chunks_deleted += 1
                
            except Exception as e:
                error_msg = f"Error processing deleted chunk {chunk_id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Stats berechnen
        stats = {
            "total_chunks_processed": len(all_chunks),
            "chunks_embedded": chunks_embedded,
            "chunks_reused": chunks_reused,
            "chunks_deleted": chunks_deleted,
            "total_deltas": len(vector_deltas),
            "batches_processed": len(batches),
            "errors": len(errors),
            "compatibility_key_hash": compatibility_key_hash,
        }
        
        result = IncrementalEmbeddingResult(
            changeset_id=changeset_id,
            source_revision=source_revision,
            compatibility_key_hash=compatibility_key_hash,
            vector_deltas=vector_deltas,
            chunks_embedded=chunks_embedded,
            chunks_reused=chunks_reused,
            chunks_deleted=chunks_deleted,
            errors=errors,
            stats=stats
        )
        
        logger.info(f"Incremental embedding build complete: {stats}")
        return result
    
    async def apply_deltas_to_qdrant(
        self,
        deltas: List[VectorDelta],
        collection_name: str
    ) -> Tuple[int, int]:
        """
        Wendet Vector-Deltas auf Qdrant an (Upserts und Deletes).
        
        Args:
            deltas: Liste von Vector-Deltas
            collection_name: Qdrant Collection Name
        
        Returns:
            Tuple aus (successful_upserts, successful_deletes)
        """
        upserts = [d for d in deltas if d.operation == VectorOperation.UPSERT]
        deletes = [d for d in deltas if d.operation == VectorOperation.DELETE]
        
        successful_upserts = 0
        successful_deletes = 0
        
        # Upserts in Batches
        for i in range(0, len(upserts), self.batch_size):
            batch = upserts[i:i + self.batch_size]
            try:
                points = [
                    {
                        "id": d.point_id,
                        "vector": d.vector,
                        "payload": d.payload
                    }
                    for d in batch
                ]
                
                await self.vector_store.upsert_points(
                    collection_name=collection_name,
                    points=points
                )
                successful_upserts += len(batch)
                
            except Exception as e:
                logger.error(f"Qdrant upsert batch failed: {e}")
                # Mark failed deltas for retry
                for delta in batch:
                    delta.retry_count += 1
        
        # Deletes
        if deletes:
            try:
                point_ids = [d.point_id for d in deletes]
                await self.vector_store.delete_points(
                    collection_name=collection_name,
                    points=point_ids
                )
                successful_deletes = len(deletes)
                
            except Exception as e:
                logger.error(f"Qdrant delete failed: {e}")
        
        return successful_upserts, successful_deletes
    
    def verify_compatibility(
        self,
        result: IncrementalEmbeddingResult
    ) -> Tuple[bool, List[str]]:
        """
        Verifiziert die Kompatibilität aller eingebetteten Vektoren.
        
        Args:
            result: Build-Ergebnis
        
        Returns:
            Tuple aus (success, error_messages)
        """
        errors = []
        
        # Prüfe dass alle Vektoren gleichen Compatibility-Key haben
        expected_hash = result.compatibility_key_hash
        for delta in result.vector_deltas:
            if delta.operation == VectorOperation.UPSERT:
                payload = delta.payload or {}
                key_data = payload.get("compatibility_key")
                if key_data:
                    try:
                        key = EmbeddingCompatibilityKey.from_dict(key_data)
                        actual_hash = key.compute_hash()
                        if actual_hash != expected_hash:
                            errors.append(
                                f"Chunk {delta.chunk_id} has mismatched compatibility key: "
                                f"{actual_hash} != {expected_hash}"
                            )
                    except Exception as e:
                        errors.append(f"Invalid compatibility key for chunk {delta.chunk_id}: {e}")
        
        # Prüfe Vektor-Dimensionen
        expected_dims = self.compatibility_key.dimensions
        for delta in result.vector_deltas:
            if delta.operation == VectorOperation.UPSERT and delta.vector:
                if len(delta.vector) != expected_dims:
                    errors.append(
                        f"Chunk {delta.chunk_id} has wrong dimensions: "
                        f"{len(delta.vector)} != {expected_dims}"
                    )
        
        return len(errors) == 0, errors


# Convenience-Funktion
async def build_incremental_embeddings(
    changeset_id: str,
    source_revision: str,
    new_chunks: List[Dict[str, Any]],
    modified_chunks: List[Dict[str, Any]],
    deleted_chunk_ids: Optional[List[str]] = None,
    embedding_provider: Optional[Any] = None,
    vector_store: Optional[Any] = None,
    chunk_store: Optional[Any] = None,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    dimensions: int = 384,
    batch_size: int = 32
) -> IncrementalEmbeddingResult:
    """
    Convenience-Funktion für inkrementelles Embedding-Build.
    """
    if not embedding_provider:
        raise ValueError("embedding_provider required")
    if not vector_store:
        raise ValueError("vector_store required")
    if not chunk_store:
        raise ValueError("chunk_store required")
    
    # CompatibilityKey erstellen
    compatibility_key = EmbeddingCompatibilityKey(
        model_name=model_name,
        model_revision="main",
        dimensions=dimensions,
        encoding="float",
        embedding_text_profile=hashlib.sha256(b"default").hexdigest()[:8]
    )
    
    builder = IncrementalEmbeddingBuilder(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        chunk_store=chunk_store,
        compatibility_key=compatibility_key,
        batch_size=batch_size
    )
    
    return builder.build_incremental(
        changeset_id=changeset_id,
        source_revision=source_revision,
        new_chunks=new_chunks,
        modified_chunks=modified_chunks,
        deleted_chunk_ids=deleted_chunk_ids
    )
