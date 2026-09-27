"""CodeCompass graph artifacts for workers under delegated access (WCRB-010).

Graph indices live in the Hub's data volume, which workers do not mount, so a
worker's graph tools failed with ``graph_artifact_not_materialized``. Now:

- the Hub serves an admitted artifact by its sha256 to a registered worker
  that presents a signed capability addressed to it (``audience``) whose
  ``allowed_index_ids`` contain an index binding that very digest;
- the worker materializes it at the admitted ``local_path`` inside its own
  artifact root (mirroring the Hub's layout, so a graph and its metrics stay
  side by side), and the unchanged resolver hash check verifies it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ARTIFACT_PATH = "/api/codecompass/worker-artifacts/{sha256}"
CAPABILITY_HEADER = "X-Ananta-CodeCompass-Capability"
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactAccessError(ValueError):
    """An artifact that must not be served or could not be fetched."""


def encode_capability(capability: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(dict(capability), sort_keys=True).encode("utf-8")).decode("ascii")


def decode_capability(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(str(value or "").encode("ascii")))
    except (ValueError, UnicodeError) as error:
        raise ArtifactAccessError("capability_header_invalid") from error
    if not isinstance(decoded, dict):
        raise ArtifactAccessError("capability_header_invalid")
    return decoded


# --- Hub side ----------------------------------------------------------------------------------


def locate_admitted_artifact(capability: Mapping[str, Any], sha256: str, *, get_index: Callable[[str], Any],
                             resolver: Any) -> Path:
    """The verified local path of the artifact ``sha256`` within the capability's indices."""
    if not _SHA256.match(str(sha256 or "")):
        raise ArtifactAccessError("artifact_digest_invalid")
    for index_id in list(capability.get("allowed_index_ids") or []):
        index = get_index(str(index_id))
        binding = dict((getattr(index, "index_metadata", None) or {}).get("graph_artifacts") or {})
        references = [binding.get("graph_index"), binding.get("visual_metrics")]
        if not any(isinstance(ref, Mapping) and ref.get("sha256") == sha256 for ref in references):
            continue
        graph_path, metrics_path = resolver.resolve_artifacts(index)  # verifies both hashes
        return graph_path if isinstance(references[0], Mapping) and references[0].get("sha256") == sha256 \
            else metrics_path
    raise ArtifactAccessError("artifact_not_granted")


# --- Worker side -------------------------------------------------------------------------------


class WorkerArtifactMaterializer:
    """Fetches an admitted artifact from the Hub to its admitted path inside the worker's artifact root."""

    def __init__(self, *, hub_url: str, headers: Mapping[str, str], capability: Mapping[str, Any],
                 artifact_root: Path, opener: Callable[..., Any] = urllib.request.urlopen,
                 timeout: float = 120.0) -> None:
        self._hub = str(hub_url or "").rstrip("/")
        if not self._hub.startswith(("http://", "https://")):
            raise ArtifactAccessError("artifact_hub_url_invalid")
        self._headers = {**dict(headers), CAPABILITY_HEADER: encode_capability(capability)}
        self._root = Path(artifact_root).resolve()
        self._opener = opener
        self._timeout = float(timeout)

    def _target(self, sha256: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        if (not _SHA256.match(str(sha256 or "")) or not local_path.is_absolute() or ".." in local_path.parts
                or local_path.name.startswith(".")):
            raise ArtifactAccessError("artifact_reference_invalid")
        try:
            local_path.relative_to(self._root)
        except ValueError:
            raise ArtifactAccessError("artifact_reference_outside_root") from None
        return local_path

    def materialize(self, sha256: str, local_path: Path) -> Path:
        target = self._target(sha256, local_path)
        if target.is_file():
            return target  # the resolver verifies its hash on every use
        url = self._hub + ARTIFACT_PATH.format(sha256=urllib.parse.quote(sha256))
        request = urllib.request.Request(url, headers=self._headers)
        try:
            with self._opener(request, timeout=self._timeout) as response:
                data = response.read(MAX_ARTIFACT_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise ArtifactAccessError(f"artifact_fetch_http_{int(error.code or 0)}") from None
        except OSError as error:
            raise ArtifactAccessError("artifact_fetch_failed") from error
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactAccessError("artifact_too_large")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ArtifactAccessError("artifact_fetch_digest_mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(self._root):  # no symlinked directory escapes
            raise ArtifactAccessError("artifact_reference_outside_root")
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".fetch-")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target


def worker_materializer(artifact_root: Path) -> WorkerArtifactMaterializer | None:
    """The materializer of the current step: only on a worker, only with a received capability."""
    from agent.config import settings
    from agent.services.codecompass_task_capability import current_task_capability

    capability = current_task_capability()
    if settings.role != "worker" or capability is None:
        return None
    from agent.services.worker_hub_headers import registered_worker_hub_headers

    try:
        return WorkerArtifactMaterializer(hub_url=str(settings.hub_url or ""), headers=registered_worker_hub_headers(),
                                          capability=capability, artifact_root=artifact_root)
    except (ArtifactAccessError, ValueError):
        return None
