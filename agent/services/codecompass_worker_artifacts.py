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
INDEX_ARTIFACT_PATH = "/api/codecompass/worker-index-artifacts/{index_id}/{role}"
CAPABILITY_HEADER = "X-Ananta-CodeCompass-Capability"
ARTIFACT_DIGEST_HEADER = "X-Artifact-SHA256"
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INDEX_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Retrieval output roles a delegated worker may pull from the Hub. Graph roles
# keep their dedicated artifact endpoint (WCRB-010).
RETRIEVAL_OUTPUT_ROLES = ("index", "details", "relations")


class ArtifactAccessError(ValueError):
    """An artifact that must not be served or could not be fetched."""


def retrieval_output_filenames() -> dict[str, str]:
    """``role -> filename`` of the retrieval outputs, from the closed artifact contract."""
    from agent.services.knowledge_index_worker_artifact_contract import (
        OUTPUT_FILENAMES,
    )

    return {role: OUTPUT_FILENAMES[role] for role in RETRIEVAL_OUTPUT_ROLES}


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


def locate_admitted_index_output(capability: Mapping[str, Any], knowledge_index_id: str, role: str, *,
                                 get_index: Callable[[str], Any]) -> tuple[Path, str]:
    """The verified path and content digest of one retrieval output of a granted index.

    Only a role of the closed retrieval contract and only an index in the
    capability's ``allowed_index_ids`` is served; the path is derived from the
    index record, never from the caller, and a missing role file is a 404-able
    ``artifact_not_found`` so a worker can simply skip an absent output.
    """
    filenames = retrieval_output_filenames()
    filename = filenames.get(str(role or ""))
    if filename is None:
        raise ArtifactAccessError("artifact_role_invalid")
    index_id = str(knowledge_index_id or "")
    allowed = {str(candidate) for candidate in list(capability.get("allowed_index_ids") or [])}
    if not index_id or index_id not in allowed:
        raise ArtifactAccessError("artifact_not_granted")
    index = get_index(index_id)
    if index is None:
        raise ArtifactAccessError("artifact_not_granted")
    output_dir_raw = str(getattr(index, "output_dir", None) or "").strip()
    if not output_dir_raw:
        raise ArtifactAccessError("artifact_output_dir_missing")
    output_dir = Path(output_dir_raw)
    if not output_dir.is_absolute():
        raise ArtifactAccessError("artifact_output_dir_missing")
    candidate = output_dir / filename
    if candidate.is_symlink() or not candidate.is_file():
        raise ArtifactAccessError("artifact_not_found")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactAccessError("artifact_not_found") from error
    if resolved.parent != output_dir.resolve():
        raise ArtifactAccessError("artifact_not_found")
    if resolved.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ArtifactAccessError("artifact_too_large")
    return resolved, _file_sha256(resolved)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


class WorkerIndexArtifactMaterializer:
    """Fetches one admitted retrieval output (index/details/relations) from the Hub.

    The Hub owns the index; a delegated worker's own data root does not hold the
    output, so ``KnowledgeIndexRetrievalService`` fetches the file once, to the
    index's own admitted path, and then reads it locally. The digest the Hub
    computed is verified against the response body.
    """

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

    def _target(self, index_id: str, filename: str, local_path: Path) -> Path:
        if not _INDEX_ID.match(str(index_id or "")):
            raise ArtifactAccessError("artifact_index_id_invalid")
        local_path = Path(local_path)
        if (not local_path.is_absolute() or ".." in local_path.parts or local_path.name != filename
                or local_path.name.startswith(".")):
            raise ArtifactAccessError("artifact_reference_invalid")
        try:
            local_path.relative_to(self._root)
        except ValueError:
            raise ArtifactAccessError("artifact_reference_outside_root") from None
        return local_path

    def materialize(self, index_id: str, role: str, local_path: Path) -> Path:
        filename = retrieval_output_filenames().get(str(role or ""))
        if filename is None:
            raise ArtifactAccessError("artifact_role_invalid")
        target = self._target(index_id, filename, local_path)
        if target.is_file():
            return target
        url = self._hub + INDEX_ARTIFACT_PATH.format(index_id=urllib.parse.quote(str(index_id), safe=""),
                                                     role=urllib.parse.quote(str(role), safe=""))
        request = urllib.request.Request(url, headers=self._headers)
        try:
            with self._opener(request, timeout=self._timeout) as response:
                data = response.read(MAX_ARTIFACT_BYTES + 1)
                announced = str(getattr(getattr(response, "headers", None), "get", lambda *_: "")(
                    ARTIFACT_DIGEST_HEADER) or "")
        except urllib.error.HTTPError as error:
            raise ArtifactAccessError(f"artifact_fetch_http_{int(error.code or 0)}") from None
        except OSError as error:
            raise ArtifactAccessError("artifact_fetch_failed") from error
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactAccessError("artifact_too_large")
        digest = hashlib.sha256(data).hexdigest()
        if announced and announced != digest:
            raise ArtifactAccessError("artifact_digest_header_mismatch")
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


def index_output_materializer(artifact_root: Path) -> WorkerIndexArtifactMaterializer | None:
    """The retrieval-output materializer of the current step: worker role plus received capability."""
    from agent.config import settings
    from agent.services.codecompass_task_capability import current_task_capability

    capability = current_task_capability()
    if settings.role != "worker" or capability is None:
        return None
    from agent.services.worker_hub_headers import registered_worker_hub_headers

    try:
        return WorkerIndexArtifactMaterializer(hub_url=str(settings.hub_url or ""),
                                               headers=registered_worker_hub_headers(), capability=capability,
                                               artifact_root=artifact_root)
    except (ArtifactAccessError, ValueError):
        return None


def try_materialize_index_outputs(knowledge_index: Any) -> bool:
    """Fetch a granted completed index's retrieval outputs to this worker's data root.

    Returns ``True`` when the index records file is present afterwards. It is a
    no-op (``False``) on the Hub, without a received capability, for an index
    outside the capability's scope, or when the Hub does not offer the output -
    the caller keeps its existing degraded "skip this index" behaviour.
    """
    from agent.config import settings
    from agent.services.codecompass_task_capability import current_task_capability

    if settings.role != "worker":
        return False
    capability = current_task_capability()
    if not isinstance(capability, Mapping):
        return False
    index_id = str(getattr(knowledge_index, "id", "") or "")
    allowed = {str(candidate) for candidate in list(capability.get("allowed_index_ids") or [])}
    if not index_id or index_id not in allowed:
        return False
    output_dir_raw = str(getattr(knowledge_index, "output_dir", None) or "").strip()
    if not output_dir_raw:
        return False
    output_dir = Path(output_dir_raw)
    artifact_root = (Path(settings.data_dir) / "knowledge_indices").resolve()
    materializer = index_output_materializer(artifact_root)
    if materializer is None:
        return False
    filenames = retrieval_output_filenames()
    for role in RETRIEVAL_OUTPUT_ROLES:
        target = output_dir / filenames[role]
        if target.is_file():
            continue
        try:
            materializer.materialize(index_id, role, target)
        except ArtifactAccessError as error:
            if str(error) == "artifact_fetch_http_404":
                continue  # the Hub does not offer this output role
            return False
        except ValueError:
            return False
    return (output_dir / filenames["index"]).is_file()

