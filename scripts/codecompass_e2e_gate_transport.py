"""In-process artifact transport for ``scripts/generate_codecompass_e2e_gate.py``.

The gate runs Hub and Worker in one isolated process. These adapters stand in
for the network transport between them: an artifact server exposing the
artifacts blueprint and a downloader that reads the Worker-published versions
from the isolated artifact store with digest verification.
"""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping


class IsolatedArtifactStoreDownloader:
    """Copy Worker-published artifacts from the isolated gate store.

    Generic ``/artifacts/<id>/content`` hides capability-bound worker
    outputs. Production uses the internal output-capability route; this
    in-process adapter reads the same published versions the Worker stored.
    """

    def download_to_path(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        destination: Path,
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: Any | None = None,
    ) -> None:
        del worker_url, worker_token, source_access_manifest, job_id, transfer_deadline
        from agent.repository import artifact_repo, artifact_version_repo

        artifact_id = str(reference.get("artifact_id") or "").strip()
        expected_hash = str(reference.get("sha256") or "").lower()
        expected_size = reference.get("size_bytes")
        artifact = artifact_repo.get_by_id(artifact_id)
        version_id = str(getattr(artifact, "latest_version_id", "") or "").strip()
        version = artifact_version_repo.get_by_id(version_id) if version_id else None
        storage_path = Path(str(getattr(version, "storage_path", "") or ""))
        if artifact is None or version is None or not storage_path.is_file():
            raise RuntimeError("codecompass_gate_worker_artifact_missing")
        content = storage_path.read_bytes()
        if (
            not isinstance(expected_size, int)
            or len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_hash
        ):
            raise RuntimeError("codecompass_gate_worker_artifact_digest_mismatch")
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("codecompass_gate_artifact_staging_conflict")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


@contextmanager
def artifact_server(token: str) -> Iterator[str]:
    from flask import Flask
    from werkzeug.serving import WSGIRequestHandler, make_server

    from agent.routes.artifacts import artifacts_bp

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            del code, size

    app = Flask("codecompass-e2e-artifact-worker")
    app.secret_key = "codecompass-e2e-flask-secret-000000000000000000"
    app.config.update(AGENT_TOKEN=token, TESTING=False)
    app.register_blueprint(artifacts_bp)
    server = make_server(
        "127.0.0.1",
        0,
        app,
        threaded=True,
        request_handler=QuietHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
