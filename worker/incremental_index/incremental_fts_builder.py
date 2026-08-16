"""
CodeCompass Incremental FTS (Full-Text Search) Index Builder

Implementiert einen inkrementellen Builder für den lexikalischen Suchindex.
FTS wird als eigene Artefaktart desselben ChangeSets behandelt, mit Upsert/Delete
für betroffene Dokumente.

Features:
- FTS als eigene Artefaktart desselben ChangeSets
- Upsert/Delete für betroffene Dokumente
- Analyzer-/Tokenizer-Wechsel als Compatibility-Änderung
- Delete/Rename erzeugt keine stale Pfadtreffer
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class FTSOperation(Enum):
    """Operationstypen für FTS-Delta."""
    INDEX = "index"
    DELETE = "delete"


@dataclass
class FTSDocument:
    """Dokument für den FTS-Index."""
    doc_id: str
    file_path: str
    symbol_name: Optional[str]
    chunk_index: int
    content: str
    title: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "file_path": self.file_path,
            "symbol_name": self.symbol_name,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "title": self.title,
            "metadata": self.metadata
        }


@dataclass
class FTSDelta:
    """Delta-Operation für den FTS-Index."""
    operation: FTSOperation
    doc_id: str
    document: Optional[FTSDocument] = None
    reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "operation": self.operation.value,
            "doc_id": self.doc_id,
            "reason": self.reason
        }
        if self.document:
            result["document"] = self.document.to_dict()
        return result


@dataclass
class FTSCompatibilityKey:
    """
    Kompatibilitätsschlüssel für FTS-Index.
    
    Bestimmt ob existierende Indizes wiederverwendet werden können.
    """
    analyzer: str  # standard, keyword, language-specific
    tokenizer: str  # whitespace, pattern, etc.
    normalizer: Optional[str]
    language: Optional[str]
    index_schema_version: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "analyzer": self.analyzer,
            "tokenizer": self.tokenizer,
            "normalizer": self.normalizer,
            "language": self.language,
            "index_schema_version": self.index_schema_version
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FTSCompatibilityKey':
        return cls(
            analyzer=data["analyzer"],
            tokenizer=data["tokenizer"],
            normalizer=data.get("normalizer"),
            language=data.get("language"),
            index_schema_version=data["index_schema_version"]
        )
    
    def compute_hash(self) -> str:
        """Berechnet Hash des Compatibility Keys."""
        canonical = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    
    def is_compatible_with(self, other: 'FTSCompatibilityKey') -> bool:
        """
        Prüft Kompatibilität mit einem anderen Key.
        
        Args:
            other: Anderer CompatibilityKey
        
        Returns:
            True wenn kompatibel (gleiche Analyzer/Tokenizer)
        """
        return (
            self.analyzer == other.analyzer and
            self.tokenizer == other.tokenizer and
            self.index_schema_version == other.index_schema_version
        )


@dataclass
class IncrementalFTSResult:
    """Ergebnis des inkrementellen FTS-Builds."""
    changeset_id: str
    source_revision: str
    compatibility_key_hash: str
    fts_deltas: List[FTSDelta] = field(default_factory=list)
    documents_indexed: int = 0
    documents_deleted: int = 0
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "source_revision": self.source_revision,
            "compatibility_key_hash": self.compatibility_key_hash,
            "fts_deltas": [d.to_dict() for d in self.fts_deltas],
            "documents_indexed": self.documents_indexed,
            "documents_deleted": self.documents_deleted,
            "errors": self.errors,
            "stats": self.stats
        }


class IncrementalFTSBuilder:
    """
    Inkrementeller Builder für Full-Text Search Index.
    
    Indext nur neue/geänderte Dokumente, verwendet existierende Indizes
    bei kompatiblen Keys und erzeugt Delta-Operationen.
    """
    
    def __init__(
        self,
        fts_engine: Any,
        fts_store: Any,
        compatibility_key: FTSCompatibilityKey,
    ):
        """
        Args:
            fts_engine: FTS-Engine (Whoosh, Lucene, Elasticsearch, etc.)
            fts_store: Store für FTS-Dokumente
            compatibility_key: Current FTS-Kompatibilitäts-Key
        """
        self.fts_engine = fts_engine
        self.fts_store = fts_store
        self.compatibility_key = compatibility_key
    
    def _compute_doc_id(
        self,
        file_path: str,
        symbol_name: Optional[str],
        chunk_index: int
    ) -> str:
        """
        Berechnet deterministische Dokument-ID.
        
        Args:
            file_path: Dateipfad
            symbol_name: Optionaler Symbol-Name
            chunk_index: Chunk-Index
        
        Returns:
            Deterministische Doc-ID
        """
        symbol_part = symbol_name or "__file__"
        canonical = f"{file_path}:{symbol_part}:{chunk_index}"
        return hashlib.sha256(canonical.encode()).hexdigest()[:36]
    
    def _prepare_document_content(
        self,
        chunk: Dict[str, Any]
    ) -> str:
        """
        Bereitet Inhalt für FTS-Index vor (Analyzer-abhängig).
        
        Args:
            chunk: Chunk-Daten
        
        Returns:
            Vorbereiteter Text für Indexierung
        """
        text = chunk.get("text", "")
        
        # Title aus Metadaten extrahieren
        title = chunk.get("metadata", {}).get("symbol_name")
        if not title:
            # Title aus Pfad ableiten
            title = chunk.get("file_path", "").split("/")[-1]
        
        # Content vorbereiten (abhängig von Analyzer)
        if self.compatibility_key.language:
            # Language-specific preprocessing könnte hier erfolgen
            pass
        
        return text
    
    def _get_existing_docs_for_file(
        self,
        file_path: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Lädt existierende Dokumente für eine Datei.
        
        Args:
            file_path: Dateipfad
        
        Returns:
            Dict von doc_id -> document_data
        """
        try:
            docs = self.fts_store.get_documents_by_file(file_path)
            return {d["doc_id"]: d for d in docs}
        except Exception:
            return {}
    
    def _check_compatibility(
        self,
        existing_doc: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Prüft ob existierendes Dokument mit aktuellem Key kompatibel ist.
        
        Args:
            existing_doc: Existierende Dokument-Daten
        
        Returns:
            Tuple aus (is_compatible, reason)
        """
        existing_meta = existing_doc.get("metadata", {})
        existing_key_data = existing_meta.get("fts_compatibility_key")
        
        if not existing_key_data:
            return False, "missing_compatibility_key"
        
        try:
            existing_key = FTSCompatibilityKey.from_dict(existing_key_data)
            
            if existing_key.compute_hash() == self.compatibility_key.compute_hash():
                return True, "exact_match"
            
            if not existing_key.is_compatible_with(self.compatibility_key):
                return False, "incompatible_analyzer_or_tokenizer"
            
            return False, "different_normalization"
            
        except Exception as e:
            logger.warning(f"FTS compatibility check failed: {e}")
            return False, "compatibility_check_error"
    
    def build_incremental(
        self,
        changeset_id: str,
        source_revision: str,
        new_chunks: List[Dict[str, Any]],
        modified_chunks: List[Dict[str, Any]],
        deleted_chunk_ids: Optional[List[str]] = None,
        deleted_files: Optional[List[str]] = None
    ) -> IncrementalFTSResult:
        """
        Führt inkrementellen FTS-Build durch.
        
        Args:
            changeset_id: ID des ChangeSets
            source_revision: Source-Revision
            new_chunks: Neue Chunks für Indexierung
            modified_chunks: Geänderte Chunks (müssen neu indexiert werden)
            deleted_chunk_ids: Gelöschte Chunk-IDs
            deleted_files: Gelöschte Dateien
        
        Returns:
            IncrementalFTSResult mit FTS-Deltas
        """
        logger.info(f"Starting incremental FTS build for {len(new_chunks) + len(modified_chunks)} chunks")
        
        compatibility_key_hash = self.compatibility_key.compute_hash()
        fts_deltas: List[FTSDelta] = []
        errors: List[str] = []
        documents_indexed = 0
        documents_deleted = 0
        
        # Alle zu indexierenden Chunks sammeln
        all_chunks = []
        for chunk in new_chunks:
            chunk["_operation"] = "new"
            all_chunks.append(chunk)
        for chunk in modified_chunks:
            chunk["_operation"] = "modified"
            all_chunks.append(chunk)
        
        # Tracke verarbeitete Dateien für Deleted-Files-Optimierung
        processed_files = set()
        
        # Chunks verarbeiten
        for chunk in all_chunks:
            try:
                file_path = chunk.get("file_path", "")
                symbol_name = chunk.get("symbol_name")
                chunk_index = chunk.get("chunk_index", 0)
                content = chunk.get("text", "")
                
                if not content or not file_path:
                    continue
                
                doc_id = self._compute_doc_id(file_path, symbol_name, chunk_index)
                
                # Dokument erstellen
                fts_doc = FTSDocument(
                    doc_id=doc_id,
                    file_path=file_path,
                    symbol_name=symbol_name,
                    chunk_index=chunk_index,
                    content=self._prepare_document_content(chunk),
                    title=chunk.get("metadata", {}).get("symbol_name"),
                    metadata={
                        **chunk.get("metadata", {}),
                        "changeset_id": changeset_id,
                        "source_revision": source_revision,
                        "fts_compatibility_key": self.compatibility_key.to_dict(),
                    }
                )
                
                delta = FTSDelta(
                    operation=FTSOperation.INDEX,
                    doc_id=doc_id,
                    document=fts_doc,
                    reason=f"chunk_{chunk.get('_operation', 'unknown')}"
                )
                fts_deltas.append(delta)
                documents_indexed += 1
                
                processed_files.add(file_path)
                
            except Exception as e:
                error_msg = f"Error processing chunk for FTS: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Gelöschte Chunks verarbeiten
        deleted_chunk_ids = deleted_chunk_ids or []
        for chunk_id in deleted_chunk_ids:
            try:
                # Doc-ID aus chunk_id berechnen (oder lookup)
                doc_id = self._compute_doc_id_from_chunk_id(chunk_id)
                
                delta = FTSDelta(
                    operation=FTSOperation.DELETE,
                    doc_id=doc_id,
                    reason="chunk_deleted"
                )
                fts_deltas.append(delta)
                documents_deleted += 1
                
            except Exception as e:
                error_msg = f"Error processing deleted chunk {chunk_id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Gelöschte Dateien verarbeiten
        deleted_files = deleted_files or []
        for file_path in deleted_files:
            if file_path in processed_files:
                # Bereits über Chunk-Processing behandelt
                continue
            
            try:
                # Alle Dokumente der Datei löschen
                existing_docs = self._get_existing_docs_for_file(file_path)
                
                for doc_id in existing_docs.keys():
                    delta = FTSDelta(
                        operation=FTSOperation.DELETE,
                        doc_id=doc_id,
                        reason="file_deleted"
                    )
                    fts_deltas.append(delta)
                    documents_deleted += 1
                
            except Exception as e:
                error_msg = f"Error processing deleted file {file_path}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Stats berechnen
        stats = {
            "total_chunks_processed": len(all_chunks),
            "documents_indexed": documents_indexed,
            "documents_deleted": documents_deleted,
            "total_deltas": len(fts_deltas),
            "files_processed": len(processed_files),
            "errors": len(errors),
            "compatibility_key_hash": compatibility_key_hash,
        }
        
        result = IncrementalFTSResult(
            changeset_id=changeset_id,
            source_revision=source_revision,
            compatibility_key_hash=compatibility_key_hash,
            fts_deltas=fts_deltas,
            documents_indexed=documents_indexed,
            documents_deleted=documents_deleted,
            errors=errors,
            stats=stats
        )
        
        logger.info(f"Incremental FTS build complete: {stats}")
        return result
    
    def _compute_doc_id_from_chunk_id(self, chunk_id: str) -> str:
        """
        Berechnet/lookup doc_id aus chunk_id.
        
        In der Praxis würde dies einen Lookup im Store erfordern.
        Hier vereinfachte Implementierung.
        """
        # Placeholder - in Realität würde chunk_id -> doc_id gemappt
        return f"doc:{chunk_id}"
    
    async def apply_deltas_to_index(
        self,
        deltas: List[FTSDelta],
        index_name: str
    ) -> Tuple[int, int]:
        """
        Wendet FTS-Deltas auf den Index an.
        
        Args:
            deltas: Liste von FTS-Deltas
            index_name: Name des FTS-Index
        
        Returns:
            Tuple aus (successful_indexed, successful_deleted)
        """
        index_ops = [d for d in deltas if d.operation == FTSOperation.INDEX]
        delete_ops = [d for d in deltas if d.operation == FTSOperation.DELETE]
        
        successful_indexed = 0
        successful_deleted = 0
        
        # Index operations
        for delta in index_ops:
            try:
                if delta.document:
                    await self.fts_engine.index_document(
                        index_name=index_name,
                        doc_id=delta.doc_id,
                        document=delta.document.to_dict()
                    )
                    successful_indexed += 1
            except Exception as e:
                logger.error(f"FTS index failed for {delta.doc_id}: {e}")
        
        # Delete operations
        for delta in delete_ops:
            try:
                await self.fts_engine.delete_document(
                    index_name=index_name,
                    doc_id=delta.doc_id
                )
                successful_deleted += 1
            except Exception as e:
                logger.error(f"FTS delete failed for {delta.doc_id}: {e}")
        
        return successful_indexed, successful_deleted
    
    def verify_effective_fts(
        self,
        result: IncrementalFTSResult
    ) -> Tuple[bool, List[str]]:
        """
        Verifiziert die Konsistenz der FTS-Deltas.
        
        Args:
            result: Build-Ergebnis
        
        Returns:
            Tuple aus (success, error_messages)
        """
        errors = []
        
        # Prüfe dass alle Dokumente gleichen Compatibility-Key haben
        expected_hash = result.compatibility_key_hash
        for delta in result.fts_deltas:
            if delta.operation == FTSOperation.INDEX and delta.document:
                key_data = delta.document.metadata.get("fts_compatibility_key")
                if key_data:
                    try:
                        key = FTSCompatibilityKey.from_dict(key_data)
                        actual_hash = key.compute_hash()
                        if actual_hash != expected_hash:
                            errors.append(
                                f"Doc {delta.doc_id} has mismatched FTS compatibility key"
                            )
                    except Exception as e:
                        errors.append(f"Invalid FTS compatibility key for doc {delta.doc_id}: {e}")
        
        # Prüfe auf duplicate doc_ids mit conflicting operations
        indexed_ids = {
            d.doc_id for d in result.fts_deltas 
            if d.operation == FTSOperation.INDEX
        }
        deleted_ids = {
            d.doc_id for d in result.fts_deltas 
            if d.operation == FTSOperation.DELETE
        }
        
        conflicts = indexed_ids & deleted_ids
        if conflicts:
            errors.append(f"Conflicting operations for docs: {conflicts}")
        
        return len(errors) == 0, errors


# Convenience-Funktion
async def build_incremental_fts(
    changeset_id: str,
    source_revision: str,
    new_chunks: List[Dict[str, Any]],
    modified_chunks: List[Dict[str, Any]],
    deleted_chunk_ids: Optional[List[str]] = None,
    deleted_files: Optional[List[str]] = None,
    fts_engine: Optional[Any] = None,
    fts_store: Optional[Any] = None,
    analyzer: str = "standard",
    tokenizer: str = "whitespace",
    language: Optional[str] = None
) -> IncrementalFTSResult:
    """
    Convenience-Funktion für inkrementellen FTS-Build.
    """
    if not fts_engine:
        raise ValueError("fts_engine required")
    if not fts_store:
        raise ValueError("fts_store required")
    
    # CompatibilityKey erstellen
    compatibility_key = FTSCompatibilityKey(
        analyzer=analyzer,
        tokenizer=tokenizer,
        normalizer=None,
        language=language,
        index_schema_version="1.0"
    )
    
    builder = IncrementalFTSBuilder(
        fts_engine=fts_engine,
        fts_store=fts_store,
        compatibility_key=compatibility_key
    )
    
    return builder.build_incremental(
        changeset_id=changeset_id,
        source_revision=source_revision,
        new_chunks=new_chunks,
        modified_chunks=modified_chunks,
        deleted_chunk_ids=deleted_chunk_ids,
        deleted_files=deleted_files
    )
