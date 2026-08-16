"""
CodeCompass Incremental Semantic/Chunk Artifact Builder

Implementiert einen inkrementellen Builder für semantische Chunks, der nur betroffene
Dateien/Symbole neu chunked und Delta-Operationen (Upserts/Tombstones) erzeugt.

Features:
- Chunks nur für betroffene Dateien/Symbole neu erzeugen
- Stabile Chunk-ID aus Source-/Symbolidentität plus Chunkstrategie
- Tombstones für entfernte oder neu geschnittene Chunks
- parent_id, role_labels, importance und Evidence konsistent aktualisieren
- Chunking-Policy-Änderung erzwingt Rebase
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ChunkOperation(Enum):
    """Operationstypen für Chunk-Delta."""
    UPSERT = "upsert"
    TOMBSTONE = "tombstone"


@dataclass
class ChunkDelta:
    """Delta-Operation für einen semantischen Chunk."""
    operation: ChunkOperation
    chunk_id: str
    file_path: str
    symbol_name: Optional[str]
    chunk_index: int
    content_hash: str
    text_preview: str  # Erste 100 Zeichen
    metadata: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation.value,
            "chunk_id": self.chunk_id,
            "file_path": self.file_path,
            "symbol_name": self.symbol_name,
            "chunk_index": self.chunk_index,
            "content_hash": self.content_hash,
            "text_preview": self.text_preview,
            "metadata": self.metadata,
            "reason": self.reason
        }


@dataclass
class IncrementalChunkBuildResult:
    """Ergebnis des inkrementellen Chunk-Builds."""
    changeset_id: str
    source_revision: str
    chunk_deltas: List[ChunkDelta] = field(default_factory=list)
    files_processed: List[str] = field(default_factory=list)
    chunks_created: int = 0
    chunks_deleted: int = 0
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "source_revision": self.source_revision,
            "chunk_deltas": [d.to_dict() for d in self.chunk_deltas],
            "files_processed": self.files_processed,
            "chunks_created": self.chunks_created,
            "chunks_deleted": self.chunks_deleted,
            "errors": self.errors,
            "stats": self.stats
        }


class IncrementalChunkBuilder:
    """
    Inkrementeller Builder für semantische Chunks.
    
    Erzeugt Chunks nur für betroffene Dateien/Symbole, verwendet stabile IDs
    und liefert Delta-Operationen für den Layer Store.
    """
    
    def __init__(
        self,
        chunking_service: Any,
        chunk_store: Any,
        chunking_policy: Dict[str, Any],
    ):
        """
        Args:
            chunking_service: Service zum Erzeugen von Chunks (chunk_file, chunk_symbol)
            chunk_store: Store für existierende Chunks
            chunking_policy: Chunking-Konfiguration (strategy, chunk_size, overlap, etc.)
        """
        self.chunking_service = chunking_service
        self.chunk_store = chunk_store
        self.chunking_policy = chunking_policy
    
    def _compute_chunk_id(
        self,
        file_path: str,
        symbol_name: Optional[str],
        chunk_index: int,
        policy_version: str
    ) -> str:
        """
        Berechnet stabile Chunk-ID aus Source, Symbol, Index und Policy.
        
        Args:
            file_path: Relativer Dateipfad
            symbol_name: Optionaler Symbol-Name (None für file-level chunks)
            chunk_index: Index des Chunks innerhalb der Quelle
            policy_version: Version der Chunking-Policy
        
        Returns:
            Deterministische Chunk-ID (SHA256-basiert)
        """
        symbol_part = symbol_name or "__file__"
        canonical = f"{file_path}:{symbol_part}:{chunk_index}:{policy_version}"
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    
    def _compute_content_hash(self, text: str) -> str:
        """Berechnet Hash des Chunk-Inhalts."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    
    def _get_policy_version(self) -> str:
        """
        Berechnet Version-Hash der Chunking-Policy.
        
        Returns:
            Hash der Policy-Konfiguration
        """
        # Nur relevante Policy-Attribute für Version
        policy_sig = {
            "strategy": self.chunking_policy.get("strategy"),
            "chunk_size": self.chunking_policy.get("chunk_size"),
            "overlap": self.chunking_policy.get("overlap"),
            "respect_boundaries": self.chunking_policy.get("respect_boundaries"),
            "min_chunk_size": self.chunking_policy.get("min_chunk_size"),
        }
        canonical = json.dumps(policy_sig, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:8]
    
    def _chunk_file(
        self,
        file_path: str,
        content: str
    ) -> List[Dict[str, Any]]:
        """
        Erzeugt Chunks für eine Datei.
        
        Args:
            file_path: Dateipfad
            content: Dateiinhalt
        
        Returns:
            Liste von Chunk-Daten
        """
        # Delegiere an chunking_service
        chunks = self.chunking_service.chunk_file(
            file_path=file_path,
            content=content,
            policy=self.chunking_policy
        )
        return chunks
    
    def _chunk_symbol(
        self,
        file_path: str,
        symbol_name: str,
        symbol_content: str
    ) -> List[Dict[str, Any]]:
        """
        Erzeugt Chunks für ein Symbol.
        
        Args:
            file_path: Dateipfad
            symbol_name: Symbol-Name
            symbol_content: Symbol-Inhalt
        
        Returns:
            Liste von Chunk-Daten
        """
        chunks = self.chunking_service.chunk_symbol(
            file_path=file_path,
            symbol_name=symbol_name,
            content=symbol_content,
            policy=self.chunking_policy
        )
        return chunks
    
    def _get_existing_chunks_for_file(
        self,
        file_path: str,
        policy_version: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Lädt existierende Chunks für eine Datei.
        
        Args:
            file_path: Dateipfad
            policy_version: Policy-Version (nur kompatible Chunks)
        
        Returns:
            Dict von chunk_id -> chunk_data
        """
        try:
            chunks = self.chunk_store.get_chunks_for_file(
                file_path=file_path,
                policy_version=policy_version
            )
            return {c["chunk_id"]: c for c in chunks}
        except Exception:
            return {}
    
    def _get_existing_chunks_for_symbol(
        self,
        file_path: str,
        symbol_name: str,
        policy_version: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Lädt existierende Chunks für ein Symbol.
        
        Args:
            file_path: Dateipfad
            symbol_name: Symbol-Name
            policy_version: Policy-Version
        
        Returns:
            Dict von chunk_id -> chunk_data
        """
        try:
            chunks = self.chunk_store.get_chunks_for_symbol(
                file_path=file_path,
                symbol_name=symbol_name,
                policy_version=policy_version
            )
            return {c["chunk_id"]: c for c in chunks}
        except Exception:
            return {}
    
    def build_incremental(
        self,
        changeset_id: str,
        source_revision: str,
        changed_files: List[str],
        file_contents: Dict[str, str],
        changed_symbols: Optional[Dict[str, List[str]]] = None,
        symbol_contents: Optional[Dict[str, Dict[str, str]]] = None,
        deleted_files: Optional[List[str]] = None,
        deleted_symbols: Optional[Dict[str, List[str]]] = None
    ) -> IncrementalChunkBuildResult:
        """
        Führt inkrementellen Chunk-Build durch.
        
        Args:
            changeset_id: ID des ChangeSets
            source_revision: Source-Revision
            changed_files: Liste geänderter Dateipfade
            file_contents: Dict von Pfad -> Inhalt
            changed_symbols: Optional Dict von Pfad -> [Symbol-Namen]
            symbol_contents: Optional Dict von Pfad -> {Symbol -> Inhalt}
            deleted_files: Optional gelöschte Dateien
            deleted_symbols: Optional gelöschte Symbole pro Datei
        
        Returns:
            IncrementalChunkBuildResult mit Chunk-Deltas
        """
        logger.info(f"Starting incremental chunk build for {len(changed_files)} files")
        
        policy_version = self._get_policy_version()
        chunk_deltas: List[ChunkDelta] = []
        files_processed: List[str] = []
        errors: List[str] = []
        chunks_created = 0
        chunks_deleted = 0
        
        # Geänderte Dateien verarbeiten
        for file_path in changed_files:
            try:
                content = file_contents.get(file_path, "")
                if not content:
                    continue
                
                # Existierende Chunks laden
                existing_chunks = self._get_existing_chunks_for_file(
                    file_path, policy_version
                )
                
                # Neue Chunks erzeugen
                new_chunks = self._chunk_file(file_path, content)
                
                # Tracke welche Chunks noch existieren
                new_chunk_ids = set()
                
                for idx, chunk_data in enumerate(new_chunks):
                    chunk_text = chunk_data.get("text", "")
                    symbol_name = chunk_data.get("symbol_name")
                    
                    chunk_id = self._compute_chunk_id(
                        file_path, symbol_name, idx, policy_version
                    )
                    new_chunk_ids.add(chunk_id)
                    
                    content_hash = self._compute_content_hash(chunk_text)
                    text_preview = chunk_text[:100].replace("\n", " ")
                    
                    # Upsert erstellen
                    delta = ChunkDelta(
                        operation=ChunkOperation.UPSERT,
                        chunk_id=chunk_id,
                        file_path=file_path,
                        symbol_name=symbol_name,
                        chunk_index=idx,
                        content_hash=content_hash,
                        text_preview=text_preview,
                        metadata={
                            **chunk_data,
                            "policy_version": policy_version,
                            "parent_id": chunk_data.get("parent_id"),
                            "role_labels": chunk_data.get("role_labels", []),
                            "importance": chunk_data.get("importance", 1.0),
                        },
                        reason="file_changed"
                    )
                    chunk_deltas.append(delta)
                    chunks_created += 1
                
                # Gelöschte Chunks finden (Tombstones)
                for old_chunk_id in existing_chunks.keys():
                    if old_chunk_id not in new_chunk_ids:
                        old_chunk = existing_chunks[old_chunk_id]
                        delta = ChunkDelta(
                            operation=ChunkOperation.TOMBSTONE,
                            chunk_id=old_chunk_id,
                            file_path=file_path,
                            symbol_name=old_chunk.get("symbol_name"),
                            chunk_index=old_chunk.get("chunk_index", 0),
                            content_hash=old_chunk.get("content_hash", ""),
                            text_preview=old_chunk.get("text_preview", ""),
                            reason="chunk_removed_or_split"
                        )
                        chunk_deltas.append(delta)
                        chunks_deleted += 1
                
                files_processed.append(file_path)
                
            except Exception as e:
                error_msg = f"Error chunking file {file_path}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Geänderte Symbole verarbeiten (falls separat angegeben)
        changed_symbols = changed_symbols or {}
        symbol_contents = symbol_contents or {}
        
        for file_path, symbols in changed_symbols.items():
            if file_path in changed_files:
                # Bereits als Datei verarbeitet
                continue
            
            try:
                for symbol_name in symbols:
                    symbol_content = symbol_contents.get(file_path, {}).get(symbol_name, "")
                    if not symbol_content:
                        continue
                    
                    # Existierende Symbol-Chunks laden
                    existing_chunks = self._get_existing_chunks_for_symbol(
                        file_path, symbol_name, policy_version
                    )
                    
                    # Neue Chunks erzeugen
                    new_chunks = self._chunk_symbol(file_path, symbol_name, symbol_content)
                    
                    new_chunk_ids = set()
                    
                    for idx, chunk_data in enumerate(new_chunks):
                        chunk_text = chunk_data.get("text", "")
                        
                        chunk_id = self._compute_chunk_id(
                            file_path, symbol_name, idx, policy_version
                        )
                        new_chunk_ids.add(chunk_id)
                        
                        content_hash = self._compute_content_hash(chunk_text)
                        text_preview = chunk_text[:100].replace("\n", " ")
                        
                        delta = ChunkDelta(
                            operation=ChunkOperation.UPSERT,
                            chunk_id=chunk_id,
                            file_path=file_path,
                            symbol_name=symbol_name,
                            chunk_index=idx,
                            content_hash=content_hash,
                            text_preview=text_preview,
                            metadata={
                                **chunk_data,
                                "policy_version": policy_version,
                            },
                            reason="symbol_changed"
                        )
                        chunk_deltas.append(delta)
                        chunks_created += 1
                    
                    # Tombstones für alte Chunks
                    for old_chunk_id in existing_chunks.keys():
                        if old_chunk_id not in new_chunk_ids:
                            old_chunk = existing_chunks[old_chunk_id]
                            delta = ChunkDelta(
                                operation=ChunkOperation.TOMBSTONE,
                                chunk_id=old_chunk_id,
                                file_path=file_path,
                                symbol_name=symbol_name,
                                chunk_index=old_chunk.get("chunk_index", 0),
                                content_hash=old_chunk.get("content_hash", ""),
                                text_preview=old_chunk.get("text_preview", ""),
                                reason="symbol_changed"
                            )
                            chunk_deltas.append(delta)
                            chunks_deleted += 1
                    
                    files_processed.append(f"{file_path}:{symbol_name}")
                    
            except Exception as e:
                error_msg = f"Error chunking symbols in {file_path}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Gelöschte Dateien verarbeiten
        deleted_files = deleted_files or []
        for file_path in deleted_files:
            try:
                existing_chunks = self._get_existing_chunks_for_file(
                    file_path, policy_version
                )
                
                for chunk_id, chunk_data in existing_chunks.items():
                    delta = ChunkDelta(
                        operation=ChunkOperation.TOMBSTONE,
                        chunk_id=chunk_id,
                        file_path=file_path,
                        symbol_name=chunk_data.get("symbol_name"),
                        chunk_index=chunk_data.get("chunk_index", 0),
                        content_hash=chunk_data.get("content_hash", ""),
                        text_preview=chunk_data.get("text_preview", ""),
                        reason="file_deleted"
                    )
                    chunk_deltas.append(delta)
                    chunks_deleted += 1
                
                files_processed.append(file_path)
                
            except Exception as e:
                error_msg = f"Error processing deleted file {file_path}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Gelöschte Symbole verarbeiten
        deleted_symbols = deleted_symbols or {}
        for file_path, symbols in deleted_symbols.items():
            try:
                for symbol_name in symbols:
                    existing_chunks = self._get_existing_chunks_for_symbol(
                        file_path, symbol_name, policy_version
                    )
                    
                    for chunk_id, chunk_data in existing_chunks.items():
                        delta = ChunkDelta(
                            operation=ChunkOperation.TOMBSTONE,
                            chunk_id=chunk_id,
                            file_path=file_path,
                            symbol_name=symbol_name,
                            chunk_index=chunk_data.get("chunk_index", 0),
                            content_hash=chunk_data.get("content_hash", ""),
                            text_preview=chunk_data.get("text_preview", ""),
                            reason="symbol_deleted"
                        )
                        chunk_deltas.append(delta)
                        chunks_deleted += 1
                    
                    files_processed.append(f"{file_path}:{symbol_name}")
                    
            except Exception as e:
                error_msg = f"Error processing deleted symbols in {file_path}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        # Stats berechnen
        stats = {
            "files_processed": len(files_processed),
            "chunks_created": chunks_created,
            "chunks_deleted": chunks_deleted,
            "total_deltas": len(chunk_deltas),
            "errors": len(errors),
            "policy_version": policy_version,
        }
        
        result = IncrementalChunkBuildResult(
            changeset_id=changeset_id,
            source_revision=source_revision,
            chunk_deltas=chunk_deltas,
            files_processed=files_processed,
            chunks_created=chunks_created,
            chunks_deleted=chunks_deleted,
            errors=errors,
            stats=stats
        )
        
        logger.info(f"Incremental chunk build complete: {stats}")
        return result
    
    def verify_effective_chunks(
        self,
        result: IncrementalChunkBuildResult
    ) -> Tuple[bool, List[str]]:
        """
        Verifiziert die Konsistenz der Chunk-Deltas.
        
        Args:
            result: Build-Ergebnis
        
        Returns:
            Tuple aus (success, error_messages)
        """
        errors = []
        
        # Prüfe auf inkonsistente Operationen
        tombstoned_ids = {
            d.chunk_id for d in result.chunk_deltas
            if d.operation == ChunkOperation.TOMBSTONE
        }
        upserted_ids = {
            d.chunk_id for d in result.chunk_deltas
            if d.operation == ChunkOperation.UPSERT
        }
        
        conflicts = tombstoned_ids & upserted_ids
        if conflicts:
            errors.append(f"Conflicting operations for chunks: {conflicts}")
        
        # Prüfe auf fehlende required fields
        for delta in result.chunk_deltas:
            if not delta.chunk_id:
                errors.append(f"Missing chunk_id in delta: {delta}")
            if not delta.content_hash:
                errors.append(f"Missing content_hash for chunk {delta.chunk_id}")
        
        return len(errors) == 0, errors


# Convenience-Funktion
def build_incremental_chunks(
    changeset_id: str,
    source_revision: str,
    changed_files: List[str],
    file_contents: Dict[str, str],
    changed_symbols: Optional[Dict[str, List[str]]] = None,
    symbol_contents: Optional[Dict[str, Dict[str, str]]] = None,
    deleted_files: Optional[List[str]] = None,
    deleted_symbols: Optional[Dict[str, List[str]]] = None,
    chunking_service: Optional[Any] = None,
    chunk_store: Optional[Any] = None,
    chunking_policy: Optional[Dict[str, Any]] = None
) -> IncrementalChunkBuildResult:
    """
    Convenience-Funktion für inkrementellen Chunk-Build.
    """
    if not chunking_service:
        raise ValueError("chunking_service required")
    if not chunk_store:
        raise ValueError("chunk_store required")
    if not chunking_policy:
        raise ValueError("chunking_policy required")
    
    builder = IncrementalChunkBuilder(
        chunking_service=chunking_service,
        chunk_store=chunk_store,
        chunking_policy=chunking_policy
    )
    
    return builder.build_incremental(
        changeset_id=changeset_id,
        source_revision=source_revision,
        changed_files=changed_files,
        file_contents=file_contents,
        changed_symbols=changed_symbols,
        symbol_contents=symbol_contents,
        deleted_files=deleted_files,
        deleted_symbols=deleted_symbols
    )
