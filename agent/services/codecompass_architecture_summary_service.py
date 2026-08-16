"""
CodeCompass Architecture Summary Service

Generates and caches semantic short descriptions for architecture nodes.
Uses deterministic metadata first, LLM summaries only when necessary.
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import hashlib
import logging
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class SummaryCacheEntry:
    """Cached summary entry."""
    node_id: str
    summary: str
    evidence_hashes: List[str]
    revision: str
    model_version: str
    prompt_version: str
    created_at: str
    expires_at: Optional[str] = None
    
    def is_valid(self) -> bool:
        """Check if cache entry is still valid."""
        if not self.expires_at:
            return True
        return datetime.fromisoformat(self.expires_at.replace('Z', '+00:00')) > datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "summary": self.summary,
            "evidence_hashes": self.evidence_hashes,
            "revision": self.revision,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at,
            "expires_at": self.expires_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SummaryCacheEntry':
        return cls(
            node_id=data["node_id"],
            summary=data["summary"],
            evidence_hashes=data.get("evidence_hashes", []),
            revision=data.get("revision", ""),
            model_version=data.get("model_version", ""),
            prompt_version=data.get("prompt_version", ""),
            created_at=data.get("created_at", ""),
            expires_at=data.get("expires_at")
        )


@dataclass
class NodeSummary:
    """Summary for an architecture node."""
    node_id: str
    summary: str
    is_derived: bool = False
    source_evidence: List[Dict[str, Any]] = field(default_factory=list)
    summary_unavailable: bool = False
    unavailability_reason: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "summary": self.summary,
            "is_derived": self.is_derived,
            "source_evidence": self.source_evidence,
            "summary_unavailable": self.summary_unavailable,
            "unavailability_reason": self.unavailability_reason
        }


class ArchitectureSummaryService:
    """Service for generating and caching architecture node summaries."""
    
    DEFAULT_CACHE_TTL_HOURS = 24
    MAX_SUMMARY_LENGTH = 200
    
    def __init__(
        self,
        cache_store: Optional[Any] = None,
        llm_client: Optional[Any] = None,
        model_version: str = "default-v1",
        prompt_version: str = "v1"
    ):
        """
        Initialize summary service.
        
        Args:
            cache_store: Optional cache store (Redis, in-memory, etc.)
            llm_client: Optional LLM client for generating summaries
            model_version: Version of the model used for summaries
            prompt_version: Version of the prompt template
        """
        self.cache_store = cache_store
        self.llm_client = llm_client
        self.model_version = model_version
        self.prompt_version = prompt_version
        self._local_cache: Dict[str, SummaryCacheEntry] = {}
    
    def get_or_generate_summary(
        self,
        node: Any,
        evidence_provider: Any
    ) -> NodeSummary:
        """
        Get summary from cache or generate it.
        
        Args:
            node: Architecture node
            evidence_provider: Provider for fetching evidence
            
        Returns:
            NodeSummary with summary text and metadata
        """
        # Try cache first
        cache_key = self._build_cache_key(node)
        cached = self._get_from_cache(cache_key)
        
        if cached and cached.is_valid():
            # Validate evidence hashes haven't changed
            if self._validate_evidence_hashes(cached, node):
                logger.debug(f"Cache hit for node {node.id}")
                return NodeSummary(
                    node_id=node.id,
                    summary=cached.summary,
                    is_derived=True,
                    source_evidence=[{"hash": h} for h in cached.evidence_hashes]
                )
        
        # Generate summary
        logger.debug(f"Cache miss for node {node.id}, generating summary")
        summary = self._generate_summary(node, evidence_provider)
        
        # Cache the result
        self._add_to_cache(cache_key, summary, node)
        
        return summary
    
    def _build_cache_key(self, node: Any) -> str:
        """Build cache key from node metadata."""
        key_parts = [
            node.id,
            node.revision or "",
            "|".join(sorted(node.evidence_hashes)),
            self.model_version,
            self.prompt_version
        ]
        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()
    
    def _get_from_cache(self, cache_key: str) -> Optional[SummaryCacheEntry]:
        """Get summary from cache."""
        # Check local cache first
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]
        
        # Check external cache store
        if self.cache_store:
            try:
                data = self.cache_store.get(f"arch_summary:{cache_key}")
                if data:
                    entry = SummaryCacheEntry.from_dict(json.loads(data))
                    self._local_cache[cache_key] = entry
                    return entry
            except Exception as e:
                logger.warning(f"Cache retrieval error: {e}")
        
        return None
    
    def _add_to_cache(
        self,
        cache_key: str,
        summary: NodeSummary,
        node: Any
    ) -> None:
        """Add summary to cache."""
        expires_at = (
            datetime.utcnow() + timedelta(hours=self.DEFAULT_CACHE_TTL_HOURS)
        ).isoformat() + "Z"
        
        entry = SummaryCacheEntry(
            node_id=node.id,
            summary=summary.summary,
            evidence_hashes=[
                e.get("hash", "") for e in summary.source_evidence
            ],
            revision=node.revision or "",
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            created_at=datetime.utcnow().isoformat() + "Z",
            expires_at=expires_at
        )
        
        # Store in local cache
        self._local_cache[cache_key] = entry
        
        # Store in external cache
        if self.cache_store:
            try:
                self.cache_store.set(
                    f"arch_summary:{cache_key}",
                    json.dumps(entry.to_dict()),
                    ex=int(self.DEFAULT_CACHE_TTL_HOURS * 3600)
                )
            except Exception as e:
                logger.warning(f"Cache storage error: {e}")
    
    def _validate_evidence_hashes(
        self,
        cached: SummaryCacheEntry,
        node: Any
    ) -> bool:
        """Validate that evidence hashes haven't changed."""
        current_hashes = set(node.evidence_hashes)
        cached_hashes = set(cached.evidence_hashes)
        return current_hashes == cached_hashes
    
    def _generate_summary(
        self,
        node: Any,
        evidence_provider: Any
    ) -> NodeSummary:
        """Generate summary for a node."""
        # First try deterministic metadata
        if node.short_summary and len(node.short_summary) > 20:
            return NodeSummary(
                node_id=node.id,
                summary=node.short_summary[:self.MAX_SUMMARY_LENGTH],
                is_derived=False,
                source_evidence=node.source_refs
            )
        
        # Try to fetch evidence
        evidence = []
        if node.evidence_hashes and evidence_provider:
            try:
                evidence = evidence_provider.fetch_evidence(
                    node.evidence_hashes,
                    node.revision
                )
            except Exception as e:
                logger.warning(f"Failed to fetch evidence for {node.id}: {e}")
        
        # Generate from evidence if available
        if evidence:
            summary = self._summarize_from_evidence(node, evidence)
            if summary:
                return NodeSummary(
                    node_id=node.id,
                    summary=summary,
                    is_derived=True,
                    source_evidence=evidence
                )
        
        # Fall back to LLM if configured
        if self.llm_client:
            summary = self._generate_llm_summary(node)
            if summary:
                return NodeSummary(
                    node_id=node.id,
                    summary=summary,
                    is_derived=True,
                    source_evidence=[{"type": "llm_generated", "model": self.model_version}]
                )
        
        # No summary available
        return NodeSummary(
            node_id=node.id,
            summary="",
            summary_unavailable=True,
            unavailability_reason="Insufficient evidence and no LLM configured"
        )
    
    def _summarize_from_evidence(
        self,
        node: Any,
        evidence: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Generate summary from evidence."""
        # Extract key information from evidence
        responsibilities = []
        relationships = []
        
        for ev in evidence:
            ev_type = ev.get("type", "")
            
            if ev_type == "responsibility":
                resp = ev.get("description", "")
                if resp:
                    responsibilities.append(resp)
            
            elif ev_type == "relationship":
                rel = ev.get("description", "")
                if rel:
                    relationships.append(rel)
        
        # Build summary
        parts = []
        
        if responsibilities:
            parts.append(f"Responsibilities: {'; '.join(responsibilities[:3])}")
        
        if relationships:
            parts.append(f"Relationships: {'; '.join(relationships[:3])}")
        
        if parts:
            summary = ". ".join(parts)
            return summary[:self.MAX_SUMMARY_LENGTH]
        
        return None
    
    def _generate_llm_summary(self, node: Any) -> Optional[str]:
        """Generate summary using LLM."""
        if not self.llm_client:
            return None
        
        # Build prompt
        prompt = self._build_summary_prompt(node)
        
        try:
            response = self.llm_client.generate(
                prompt=prompt,
                max_tokens=50,
                temperature=0.3
            )
            
            summary = response.text.strip()
            return summary[:self.MAX_SUMMARY_LENGTH] if summary else None
            
        except Exception as e:
            logger.error(f"LLM summary generation failed for {node.id}: {e}")
            return None
    
    def _build_summary_prompt(self, node: Any) -> str:
        """Build prompt for LLM summary generation."""
        return f"""Generate a concise responsibility summary (max 200 characters) for this architecture node:

Level: {node.level.value}
Title: {node.title}
Path: {node.source_refs[0]['path'] if node.source_refs else 'unknown'}
Responsibilities: {', '.join(node.responsibilities) if node.responsibilities else 'none listed'}

Summary:"""
    
    def invalidate_cache(self, node_id: str) -> None:
        """Invalidate cache for a specific node."""
        # Remove from local cache
        keys_to_remove = [
            k for k in self._local_cache.keys()
            if k.startswith(node_id)
        ]
        for key in keys_to_remove:
            del self._local_cache[key]
        
        # Remove from external cache
        if self.cache_store:
            # Would need to scan keys - implementation depends on cache store
            pass
        
        logger.info(f"Invalidated cache for node {node_id}")
    
    def invalidate_by_revision(self, revision: str) -> None:
        """Invalidate all cache entries for a revision."""
        keys_to_remove = [
            k for k, v in self._local_cache.items()
            if v.revision == revision
        ]
        for key in keys_to_remove:
            del self._local_cache[key]
        
        logger.info(f"Invalidated cache for revision {revision}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        valid_count = sum(1 for e in self._local_cache.values() if e.is_valid())
        expired_count = len(self._local_cache) - valid_count
        
        return {
            "total_entries": len(self._local_cache),
            "valid_entries": valid_count,
            "expired_entries": expired_count,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version
        }


def create_summary_service(
    cache_store: Optional[Any] = None,
    llm_client: Optional[Any] = None,
    model_version: str = "default-v1"
) -> ArchitectureSummaryService:
    """Factory function to create summary service."""
    return ArchitectureSummaryService(
        cache_store=cache_store,
        llm_client=llm_client,
        model_version=model_version
    )
