from __future__ import annotations
import re
import fnmatch
from typing import Dict, Any, List, Optional
from worker.core.context_access_policy import Sensitivity, SourceType

class SourceClassificationService:
    def __init__(self):
        # T007: Built-in secret and credential detectors
        self._secret_patterns = [
            re.compile(r"(?i)(?:api_key|password|bearer|secret|token|credential|private_key)\s*(?:[:=]|\bis\b)\s*['\"]?[\w\-\.\/]{8,}['\"]?"),
            re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
            re.compile(r"ssh-rsa AAAA[0-9A-Za-z+/]+[=]{0,3}"),
            re.compile(r"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"), # Sample JWT
        ]
        
        # Path based sensitivity rules (T006)
        self._path_rules = [
            (Sensitivity.secret, [".env", "**/secrets/**", "**/*.pem", "**/*.key", "**/credentials.xml"]),
            (Sensitivity.security_sensitive, ["**/config/**", "**/auth/**", "**/security/**"]),
            (Sensitivity.project_internal, ["src/**", "lib/**"]),
            (Sensitivity.public, ["README.md", "LICENSE", "docs/public/**"]),
        ]

    def classify_source(self, source_ref: str, source_type: Optional[SourceType] = None, content: Optional[str] = None) -> Sensitivity:
        """Classifies a source based on its reference (path), type and optionally content."""
        
        # 1. Path based classification (T006)
        for sensitivity, patterns in self._path_rules:
            for pattern in patterns:
                if fnmatch.fnmatch(source_ref, pattern):
                    # If it's a path match for secret, we can return immediately
                    if sensitivity == Sensitivity.secret:
                        return sensitivity
                    # Otherwise, content might upgrade it
                    detected_sensitivity = sensitivity
                    break
            else:
                continue
            break
        else:
            detected_sensitivity = Sensitivity.unknown

        # 2. Content based classification (T007)
        if content:
            content_sensitivity = self.detect_content_sensitivity(content)
            if content_sensitivity == Sensitivity.secret:
                return Sensitivity.secret
            if content_sensitivity != Sensitivity.unknown and detected_sensitivity == Sensitivity.unknown:
                detected_sensitivity = content_sensitivity

        # 3. Source type defaults (T004)
        if source_type == SourceType.secret_file or source_type == SourceType.env_file:
            return Sensitivity.secret
        if source_type == SourceType.config:
            return max(detected_sensitivity, Sensitivity.security_sensitive, key=lambda s: self._sensitivity_rank(s))

        return detected_sensitivity

    def detect_content_sensitivity(self, content: str) -> Sensitivity:
        """Detects if content contains secrets or credentials."""
        for pattern in self._secret_patterns:
            if pattern.search(content):
                return Sensitivity.secret
        return Sensitivity.unknown

    def redact_secrets(self, content: str) -> str:
        """Redact known secret/token patterns from text."""
        redacted = str(content or "")
        for pattern in self._secret_patterns:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

    def classify_codecompass_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """T008: Classify CodeCompass nodes with sensitivity metadata."""
        source_ref = node.get("path") or node.get("id") or ""
        source_type_val = node.get("type", "codecompass_code")
        
        # Map CodeCompass types to our SourceType
        st = SourceType.codecompass_code
        if source_type_val == "file":
             st = SourceType.codecompass_code
        elif source_type_val == "graph_node":
             st = SourceType.codecompass_graph
             
        sensitivity = self.classify_source(source_ref, st, content=node.get("content"))
        
        # Add metadata
        node["sensitivity"] = sensitivity
        node["source_type"] = st
        node["source_ref"] = source_ref
        node["source_tags"] = node.get("tags", [])
        return node

    def classify_codecompass_edge(self, edge: Dict[str, Any], source_node: Dict[str, Any], target_node: Dict[str, Any]) -> Dict[str, Any]:
        """T008: Classify CodeCompass graph edges based on connected nodes."""
        s1 = source_node.get("sensitivity", Sensitivity.unknown)
        s2 = target_node.get("sensitivity", Sensitivity.unknown)
        
        # Edge inherits the max sensitivity
        edge["sensitivity"] = max(s1, s2, key=lambda s: self._sensitivity_rank(s))
        edge["source_type"] = SourceType.codecompass_graph
        return edge

    def classify_memory_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """T009: Classify memory and artifact sources before reuse."""
        source_ref = entry.get("source_ref", "memory")
        content = entry.get("content", "")
        
        sensitivity = self.classify_source(source_ref, SourceType.memory, content=content)
        
        # Inheritance from provenance (T009)
        if "provenance" in entry and "max_source_sensitivity" in entry["provenance"]:
             prov_sens = entry["provenance"]["max_source_sensitivity"]
             sensitivity = max(sensitivity, prov_sens, key=lambda s: self._sensitivity_rank(s))

        entry["sensitivity"] = sensitivity
        entry["source_type"] = SourceType.memory
        return entry

    def _sensitivity_rank(self, s: Sensitivity) -> int:
        ranks = {
            Sensitivity.public: 0,
            Sensitivity.generated_summary: 1,
            Sensitivity.unknown: 2,
            Sensitivity.project_internal: 3,
            Sensitivity.customer_confidential: 4,
            Sensitivity.regulated_data: 5,
            Sensitivity.security_sensitive: 6,
            Sensitivity.secret: 7,
            Sensitivity.credential: 8
        }
        return ranks.get(s, 2)

_instance = None
def get_source_classification_service() -> SourceClassificationService:
    global _instance
    if _instance is None:
        _instance = SourceClassificationService()
    return _instance
