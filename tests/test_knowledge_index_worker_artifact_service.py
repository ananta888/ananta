from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_worker_artifact_service import (
    HttpKnowledgeIndexWorkerArtifactDownloader,
    KnowledgeIndexWorkerArtifactService,
    _KnowledgeIndexWorkerNoRedirectHandler,
)
from agent.services.worker_forward_transport import (
    WorkerForwardDeadlineExceeded,
    WorkerTransportDeadline,
)


class Repository:
    def __init__(self):
        self.items = {}

    def get_by_id(self, item_id):
        return self.items.get(item_id)

    def save(self, item):
        self.items[item.id] = item
        return item


class Downloader:
    def __init__(self, content_by_artifact):
        self.content_by_artifact = dict(content_by_artifact)
        self.calls = []
        self.capability_calls = []

    def download(
        self,
        *,
        worker_url,
        worker_token,
        reference,
        source_access_manifest=None,
        job_id=None,
    ):
        self.calls.append((worker_url, worker_token, dict(reference)))
        self.capability_calls.append(
            {
                "source_access_manifest": source_access_manifest,
                "job_id": job_id,
            }
        )
        content = self.content_by_artifact[reference["artifact_id"]]
        assert hashlib.sha256(content).hexdigest() == reference["sha256"]
        return content


def reference(*, artifact_id: str, role: str, filename: str, content: bytes) -> dict:
    return {
        "artifact_id": artifact_id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "media_type": "application/json" if role == "manifest" else "application/x-ndjson",
        "role": role,
        "filename": filename,
        "size_bytes": len(content),
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
    }


def declared_reference(*, role: str, filename: str, size_bytes: int) -> dict:
    """Build a metadata-only reference without allocating its declared payload."""

    return {
        "artifact_id": f"artifact-{role}",
        "sha256": "a" * 64,
        "media_type": "application/json" if role == "manifest" else "application/x-ndjson",
        "role": role,
        "filename": filename,
        "size_bytes": size_bytes,
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
    }


def completed_result(artifact_refs: list[dict]) -> dict:
    return {
        "status": "completed",
        "knowledge_index": {
            "id": "idx-1",
            "source_scope": "artifact",
            "status": "completed",
        },
        "run": {
            "id": "run-1",
            "knowledge_index_id": "idx-1",
            "status": "completed",
        },
        "results": None,
        "artifact_refs": artifact_refs,
    }


def assigned_task() -> dict:
    return {
        "assigned_agent_url": "http://worker-a:5000",
        "assigned_agent_token": "worker-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "job_id": "knowledge-index-" + "a" * 32,
                "job_type": "artifact",
                "source_scope": None,
            }
        },
    }


def bound_v2_task(*, max_output_bytes: int = 1024) -> dict:
    task = assigned_task()
    task["worker_execution_context"]["knowledge_index_job"].update(
        {
            "schema": "ananta.knowledge_index_execution_job.v2",
            "job_type": "source_records",
            "source_scope": "repo_path",
            "resources": {"max_output_bytes": max_output_bytes},
            "authority_binding": {
                "binding_digest": "b" * 64,
                "source_revision_id": "srev_" + "a" * 64,
            },
            "assignment": {"assignment_id": "assignment-a"},
            "source_access_enforcement_manifest": {
                "schema": "signed-manifest",
                "signature": "test-signature",
            },
        }
    )
    return task


def test_hub_materializes_verified_worker_outputs_and_persists_local_paths(tmp_path) -> None:
    manifest = b'{"index_record_count": 1}'
    index = b'{"file": "agent/runtime.py"}\n'
    downloader = Downloader({"artifact-manifest": manifest, "artifact-index": index})
    index_repository = Repository()
    run_repository = Repository()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=index_repository,
        knowledge_index_run_repository=run_repository,
        output_root=tmp_path,
    )
    task = {
        "assigned_agent_url": "http://worker-a:5000",
        "assigned_agent_token": "worker-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "job_id": "knowledge-index-" + "a" * 32,
                "job_type": "source_records",
                "source_scope": "artifact",
            }
        },
    }
    result = {
        "status": "completed",
        "knowledge_index": {
            "id": "idx-1",
            "source_scope": "artifact",
            "status": "completed",
            "index_metadata": {"codecompass_snapshot_revision": "b" * 64},
        },
        "run": {
            "id": "run-1",
            "knowledge_index_id": "idx-1",
            "status": "completed",
            "run_metadata": {},
        },
        "results": None,
        "artifact_refs": [
            reference(
                artifact_id="artifact-manifest",
                role="manifest",
                filename="manifest.json",
                content=manifest,
            ),
            reference(
                artifact_id="artifact-index",
                role="index",
                filename="index.jsonl",
                content=index,
            ),
        ],
    }

    materialized = service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=result,
        task=task,
    )

    output_dir = tmp_path / "artifact" / "idx-1" / "run-1"
    assert (output_dir / "manifest.json").read_bytes() == manifest
    assert (output_dir / "index.jsonl").read_bytes() == index
    assert materialized["knowledge_index"]["output_dir"] == str(output_dir)
    assert index_repository.items["idx-1"].index_metadata["codecompass_snapshot_revision"] == "b" * 64
    assert run_repository.items["run-1"].manifest_path == str(output_dir / "manifest.json")
    assert len(downloader.calls) == 2


def test_hub_rejects_completed_result_without_manifest_and_index(tmp_path) -> None:
    service = KnowledgeIndexWorkerArtifactService(
        downloader=Downloader({}),
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )
    task = {
        "assigned_agent_url": "http://worker-a:5000",
        "assigned_agent_token": "worker-token",
        "worker_execution_context": {
            "knowledge_index_job": {
                "job_id": "knowledge-index-" + "a" * 32,
                "job_type": "artifact",
                "source_scope": None,
            }
        },
    }

    with pytest.raises(ValueError, match="artifacts_incomplete"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result={
                "status": "completed",
                "knowledge_index": {"id": "idx-1"},
                "run": {"id": "run-1", "knowledge_index_id": "idx-1"},
                "results": None,
                "artifact_refs": [],
            },
            task=task,
        )


def test_hub_rejects_aggregate_unit_budget_before_any_download(tmp_path) -> None:
    class NeverDownloader:
        calls = 0

        def download(self, **_kwargs):
            self.calls += 1
            raise AssertionError("budget preflight must precede downloads")

    downloader = NeverDownloader()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )
    maximum = 128 * 1024 * 1024
    refs = [
        declared_reference(role="manifest", filename="manifest.json", size_bytes=maximum),
        declared_reference(role="index", filename="index.jsonl", size_bytes=maximum),
        declared_reference(role="details", filename="details.jsonl", size_bytes=maximum),
        declared_reference(role="relations", filename="relations.jsonl", size_bytes=1),
    ]

    with pytest.raises(ValueError, match="artifact_unit_budget_exceeded"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=completed_result(refs),
            task=assigned_task(),
        )

    assert downloader.calls == 0
    assert not (tmp_path / "artifact" / "idx-1" / "run-1").exists()
    assert not list(tmp_path.rglob(".run-1.artifacts-*"))


def test_bound_v2_global_output_budget_precedes_downloads(tmp_path) -> None:
    class NeverDownloader:
        calls = 0

        def download(self, **_kwargs):
            self.calls += 1
            raise AssertionError("global budget must precede downloads")

    downloader = NeverDownloader()
    refs = [
        declared_reference(
            role="manifest",
            filename="manifest.json",
            size_bytes=1,
        ),
        declared_reference(
            role="index",
            filename="index.jsonl",
            size_bytes=1,
        ),
    ]
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    with pytest.raises(ValueError, match="output_budget_exceeded"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=completed_result(refs),
            task=bound_v2_task(max_output_bytes=1),
        )

    assert downloader.calls == 0


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda refs: refs.append(
                {
                    **refs[0],
                    "role": "details",
                    "filename": "details.jsonl",
                }
            ),
            "artifact_ref_duplicate",
        ),
        (
            lambda refs: refs[0].update(run_id="unrelated-run"),
            "artifact_ref_unreferenced",
        ),
    ],
)
def test_result_rejects_duplicate_and_unreferenced_refs_before_download(
    tmp_path,
    mutate,
    reason,
) -> None:
    class NeverDownloader:
        calls = 0

        def download(self, **_kwargs):
            self.calls += 1
            raise AssertionError("reference preflight must precede downloads")

    refs = [
        declared_reference(
            role="manifest",
            filename="manifest.json",
            size_bytes=1,
        ),
        declared_reference(
            role="index",
            filename="index.jsonl",
            size_bytes=1,
        ),
    ]
    mutate(refs)
    downloader = NeverDownloader()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    with pytest.raises(ValueError, match=reason):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=completed_result(refs),
            task=assigned_task(),
        )

    assert downloader.calls == 0


def test_v1_global_result_budget_precedes_downloads(tmp_path) -> None:
    class NeverDownloader:
        calls = 0

        def download(self, **_kwargs):
            self.calls += 1
            raise AssertionError("v1 result budget must precede downloads")

    units = []
    refs = []
    for ordinal in range(3):
        index_id = f"idx-{ordinal}"
        run_id = f"run-{ordinal}"
        units.append(
            {
                "knowledge_index": {"id": index_id},
                "run": {
                    "id": run_id,
                    "knowledge_index_id": index_id,
                },
            }
        )
        for role, filename in (
            ("manifest", "manifest.json"),
            ("index", "index.jsonl"),
        ):
            refs.append(
                {
                    **declared_reference(
                        role=role,
                        filename=filename,
                        size_bytes=128 * 1024 * 1024,
                    ),
                    "artifact_id": f"artifact-{ordinal}-{role}",
                    "knowledge_index_id": index_id,
                    "run_id": run_id,
                }
            )
    result = {
        "status": "completed",
        "knowledge_index": None,
        "run": None,
        "results": units,
        "artifact_refs": refs,
    }
    downloader = NeverDownloader()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    with pytest.raises(ValueError, match="output_budget_exceeded"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=result,
            task=assigned_task(),
        )

    assert downloader.calls == 0


def test_result_unit_and_reference_counts_are_bounded_before_download(
    tmp_path,
) -> None:
    class NeverDownloader:
        calls = 0

        def download(self, **_kwargs):
            self.calls += 1
            raise AssertionError("count preflight must precede downloads")

    downloader = NeverDownloader()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )
    two_units = {
        "status": "completed",
        "knowledge_index": None,
        "run": None,
        "results": [
            {
                "knowledge_index": {"id": f"idx-{ordinal}"},
                "run": {
                    "id": f"run-{ordinal}",
                    "knowledge_index_id": f"idx-{ordinal}",
                },
            }
            for ordinal in range(2)
        ],
        "artifact_refs": [],
    }
    with pytest.raises(ValueError, match="result_unit_limit_exceeded"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=two_units,
            task=bound_v2_task(),
        )

    too_many_refs = completed_result(
        [
            {
                **declared_reference(
                    role="manifest",
                    filename="manifest.json",
                    size_bytes=0,
                ),
                "artifact_id": f"artifact-{ordinal}",
            }
            for ordinal in range(7)
        ]
    )
    with pytest.raises(ValueError, match="artifact_ref_limit_exceeded"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=too_many_refs,
            task=assigned_task(),
        )

    assert downloader.calls == 0


def test_hub_legacy_downloader_releases_each_payload_before_fetching_next(tmp_path) -> None:
    released: list[str] = []
    content_by_id = {
        "artifact-index": b'{"file":"a.py"}\n',
        "artifact-manifest": b'{"index_record_count":1}',
    }

    class TrackedBytes(bytes):
        def __new__(cls, content: bytes, artifact_id: str):
            instance = super().__new__(cls, content)
            instance.artifact_id = artifact_id
            return instance

        def __del__(self):
            released.append(self.artifact_id)

    class LegacyDownloader:
        def __init__(self):
            self.calls: list[str] = []

        def download(self, *, worker_url, worker_token, reference):
            if self.calls:
                assert self.calls[-1] in released
            artifact_id = reference["artifact_id"]
            self.calls.append(artifact_id)
            return TrackedBytes(content_by_id[artifact_id], artifact_id)

    downloader = LegacyDownloader()
    refs = [
        reference(
            artifact_id=artifact_id,
            role=role,
            filename=filename,
            content=content_by_id[artifact_id],
        )
        for artifact_id, role, filename in (
            ("artifact-manifest", "manifest", "manifest.json"),
            ("artifact-index", "index", "index.jsonl"),
        )
    ]
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=completed_result(refs),
        task=assigned_task(),
    )

    assert downloader.calls == ["artifact-index", "artifact-manifest"]
    assert released == downloader.calls


def test_hub_prefers_streaming_downloader_port(tmp_path) -> None:
    content_by_id = {
        "artifact-index": b'{"file":"a.py"}\n',
        "artifact-manifest": b'{"index_record_count":1}',
    }

    class StreamingDownloader:
        def __init__(self):
            self.calls: list[str] = []

        def download(self, **_kwargs):
            raise AssertionError("legacy in-memory port must not be used")

        def download_to_path(self, *, worker_url, worker_token, reference, destination):
            self.calls.append(reference["artifact_id"])
            destination.write_bytes(content_by_id[reference["artifact_id"]])

    downloader = StreamingDownloader()
    refs = [
        reference(
            artifact_id=artifact_id,
            role=role,
            filename=filename,
            content=content_by_id[artifact_id],
        )
        for artifact_id, role, filename in (
            ("artifact-manifest", "manifest", "manifest.json"),
            ("artifact-index", "index", "index.jsonl"),
        )
    ]
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=completed_result(refs),
        task=assigned_task(),
    )

    output_dir = tmp_path / "artifact" / "idx-1" / "run-1"
    assert downloader.calls == ["artifact-index", "artifact-manifest"]
    assert (output_dir / "index.jsonl").read_bytes() == content_by_id["artifact-index"]
    assert (output_dir / "manifest.json").read_bytes() == content_by_id["artifact-manifest"]


def test_default_http_downloader_streams_v1_over_generic_token_route(
    tmp_path,
) -> None:
    content = b"x" * (2 * 1024 * 1024 + 17)

    class Response:
        headers = {"Content-Length": str(len(content))}

        def __init__(self):
            self.offset = 0
            self.read_sizes: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size: int) -> bytes:
            self.read_sizes.append(size)
            chunk = content[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    response = Response()

    class Opener:
        def open(self, request, *, timeout):
            assert timeout == 60
            assert request.full_url.endswith(
                "/artifacts/artifact-index/content"
            )
            headers = {
                key.lower(): value for key, value in request.header_items()
            }
            assert headers["authorization"] == "Bearer worker-token"
            assert "x-ananta-source-access-manifest" not in headers
            return response

    destination = tmp_path / "artifact.bin"

    HttpKnowledgeIndexWorkerArtifactDownloader(opener=Opener()).download_to_path(
        worker_url="http://worker-a:5000",
        worker_token="worker-token",
        reference={
            "artifact_id": "artifact-index",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        destination=destination,
    )

    assert destination.read_bytes() == content
    assert len(response.read_sizes) >= 4
    assert max(response.read_sizes) <= 1024 * 1024


def test_default_http_downloader_enforces_absolute_slow_drip_deadline(
    tmp_path,
) -> None:
    now = [100.0]
    socket_timeouts = []

    class Socket:
        def settimeout(self, timeout):
            socket_timeouts.append(timeout)

    class Response:
        headers = {"Content-Length": "2"}
        fp = SimpleNamespace(
            raw=SimpleNamespace(_sock=Socket()),
        )

        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read1(self, _size):
            self.reads += 1
            now[0] += 0.6
            return b"x"

    response = Response()

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout == 1.0
            return response

    destination = tmp_path / "slow-drip.bin"
    deadline = WorkerTransportDeadline.after_seconds(
        1,
        monotonic_clock=lambda: now[0],
    )

    with pytest.raises(
        WorkerForwardDeadlineExceeded,
        match="worker_forward_transport_deadline_exceeded",
    ):
        HttpKnowledgeIndexWorkerArtifactDownloader(
            opener=Opener()
        ).download_to_path(
            worker_url="http://worker-a:5000",
            worker_token="worker-token",
            reference={
                "artifact_id": "artifact-index",
                "size_bytes": 2,
                "sha256": hashlib.sha256(b"xx").hexdigest(),
            },
            destination=destination,
            transfer_deadline=deadline,
        )

    assert response.reads == 2
    assert socket_timeouts == pytest.approx([1.0, 0.4])
    assert not destination.exists()


def test_default_http_downloader_uses_job_bound_output_capability_even_with_token(
    tmp_path,
) -> None:
    content = b'{"record_count":1}'
    captured = {}

    class Response:
        headers = {"Content-Length": str(len(content))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            value = getattr(self, "_content", content)
            self._content = b""
            return value

    class Opener:
        def open(self, request, *, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {
                key.lower(): value
                for key, value in request.header_items()
            }
            assert timeout == 60
            return Response()

    destination = tmp_path / "manifest.json"
    job_id = "knowledge-index-" + "a" * 32

    HttpKnowledgeIndexWorkerArtifactDownloader(opener=Opener()).download_to_path(
        worker_url="http://worker-a:5000",
        worker_token="worker-token",
        source_access_manifest={"schema": "signed-manifest"},
        job_id=job_id,
        reference={
            "artifact_id": "artifact-manifest",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "media_type": "application/json",
            "role": "manifest",
            "knowledge_index_id": "idx-1",
            "run_id": "run-1",
        },
        destination=destination,
    )

    assert captured["url"].endswith(
        "/internal/knowledge-index/output-artifacts/artifact-manifest"
    )
    assert captured["headers"]["authorization"] == (
        "Bearer worker-token"
    )
    assert captured["headers"]["x-ananta-knowledge-index-job-id"] == job_id
    assert "x-ananta-source-access-manifest" in captured["headers"]
    assert destination.read_bytes() == content


def test_default_http_downloader_rejects_redirect_without_forwarding_headers(
    tmp_path,
) -> None:
    redirect_target = "http://metadata.internal/latest/credentials"

    class RedirectResponse:
        status = 307
        headers = {"Location": redirect_target}

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    response = RedirectResponse()

    class Opener:
        def __init__(self):
            self.requests = []

        def open(self, request, *, timeout):
            assert timeout == 60
            self.requests.append(request)
            return response

    opener = Opener()
    destination = tmp_path / "redirected.json"
    job_id = "knowledge-index-" + "a" * 32

    with pytest.raises(
        ValueError,
        match="knowledge_index_worker_artifact_redirect_forbidden",
    ):
        HttpKnowledgeIndexWorkerArtifactDownloader(
            opener=opener
        ).download_to_path(
            worker_url="http://worker-a:5000",
            worker_token="worker-token",
            source_access_manifest={"schema": "signed-manifest"},
            job_id=job_id,
            reference={
                "artifact_id": "artifact-manifest",
                "size_bytes": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
                "media_type": "application/json",
                "role": "manifest",
                "knowledge_index_id": "idx-1",
                "run_id": "run-1",
            },
            destination=destination,
        )

    assert len(opener.requests) == 1
    assert opener.requests[0].full_url.startswith(
        "http://worker-a:5000/"
    )
    assert opener.requests[0].full_url != redirect_target
    assert response.closed is True
    assert not destination.exists()


def test_default_http_downloader_installs_no_redirect_handler() -> None:
    downloader = HttpKnowledgeIndexWorkerArtifactDownloader()
    handlers = [
        handler
        for handler in downloader._opener.handlers
        if isinstance(handler, _KnowledgeIndexWorkerNoRedirectHandler)
    ]

    assert len(handlers) == 1
    assert (
        handlers[0].redirect_request(
            None,
            None,
            307,
            "Temporary Redirect",
            {"Location": "http://metadata.internal/"},
            "http://metadata.internal/",
        )
        is None
    )


def test_bound_v2_result_never_downgrades_to_token_only_transport(
    tmp_path,
) -> None:
    downloader = Downloader({})
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )
    task = assigned_task()
    task["worker_execution_context"]["knowledge_index_job"][
        "schema"
    ] = "ananta.knowledge_index_execution_job.v2"

    with pytest.raises(
        ValueError,
        match="knowledge_index_worker_output_capability_required",
    ):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=completed_result([]),
            task=task,
        )

    assert downloader.calls == []


def test_hub_removes_staging_and_exposes_no_partial_output_on_failure(tmp_path) -> None:
    content_by_id = {
        "artifact-index": b'{"file":"a.py"}\n',
        "artifact-manifest": b'{"index_record_count":1}',
    }

    class FailingStreamingDownloader:
        def download_to_path(self, *, worker_url, worker_token, reference, destination):
            if reference["role"] == "manifest":
                destination.write_bytes(b"partial")
                raise RuntimeError("worker connection lost")
            destination.write_bytes(content_by_id[reference["artifact_id"]])

    refs = [
        reference(
            artifact_id=artifact_id,
            role=role,
            filename=filename,
            content=content_by_id[artifact_id],
        )
        for artifact_id, role, filename in (
            ("artifact-manifest", "manifest", "manifest.json"),
            ("artifact-index", "index", "index.jsonl"),
        )
    ]
    service = KnowledgeIndexWorkerArtifactService(
        downloader=FailingStreamingDownloader(),
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="worker connection lost"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=completed_result(refs),
            task=assigned_task(),
        )

    assert not (tmp_path / "artifact" / "idx-1" / "run-1").exists()
    assert not list(tmp_path.rglob(".run-1.artifacts-*"))


def test_hub_rejects_oversized_graph_json_before_any_download(tmp_path) -> None:
    class NeverDownloader:
        calls = 0

        def download(self, **_kwargs):
            self.calls += 1
            raise AssertionError("graph size preflight must precede downloads")

    downloader = NeverDownloader()
    refs = [
        declared_reference(role="manifest", filename="manifest.json", size_bytes=0),
        declared_reference(role="index", filename="index.jsonl", size_bytes=0),
        {
            **declared_reference(
                role="graph_index",
                filename="cc_graph_index.json",
                size_bytes=32 * 1024 * 1024 + 1,
            ),
            "media_type": "application/vnd.ananta.codecompass-graph-index+json",
        },
        {
            **declared_reference(
                role="graph_visual_metrics",
                filename="cc_graph_index.visual_metrics.json",
                size_bytes=0,
            ),
            "media_type": "application/vnd.ananta.codecompass-graph-visual-metrics+json",
        },
    ]
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )

    with pytest.raises(ValueError, match="graph_artifact_too_large"):
        service.materialize(
            job_id="knowledge-index-" + "a" * 32,
            result=completed_result(refs),
            task=assigned_task(),
        )

    assert downloader.calls == 0
    assert not list(tmp_path.rglob(".run-1.artifacts-*"))


def test_v2_nested_authority_projects_public_artifact_manifest(tmp_path) -> None:
    manifest = b'{"coverage":{},"exclusions":[]}'
    index = b'{"file":"agent/runtime.py"}\n'
    downloader = Downloader(
        {"artifact-manifest": manifest, "artifact-index": index}
    )
    index_repository = Repository()
    run_repository = Repository()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=index_repository,
        knowledge_index_run_repository=run_repository,
        output_root=tmp_path,
    )
    task = bound_v2_task(max_output_bytes=1024)
    result = completed_result(
        [
            reference(
                artifact_id="artifact-manifest",
                role="manifest",
                filename="manifest.json",
                content=manifest,
            ),
            reference(
                artifact_id="artifact-index",
                role="index",
                filename="index.jsonl",
                content=index,
            ),
        ]
    )

    materialized = service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=result,
        task=task,
    )

    public = materialized["knowledge_index"]["index_metadata"][
        "artifact_manifest"
    ]
    assert public["source_revision_id"] == "srev_" + "a" * 64
    assert public["run_id"] == "run-1"
    assert len(public["manifest_digest"]) == 64
    assert (
        materialized["run"]["run_metadata"]["artifact_manifest"]
        == public
    )
    assert materialized["knowledge_index"]["status"] == "completed"
    assert index_repository.items["idx-1"].status == (
        "pending_verification"
    )
    assert run_repository.items["run-1"].status == "pending_verification"
    pending_binding = index_repository.items["idx-1"].index_metadata[
        "knowledge_index_execution_binding"
    ]
    assert pending_binding["execution_job_schema"] == (
        "ananta.knowledge_index_execution_job.v2"
    )
    assert pending_binding["projection_state"] == "pending"
    assert downloader.capability_calls == [
        {
            "source_access_manifest": {
                "schema": "signed-manifest",
                "signature": "test-signature",
            },
            "job_id": "knowledge-index-" + "a" * 32,
        },
        {
            "source_access_manifest": {
                "schema": "signed-manifest",
                "signature": "test-signature",
            },
            "job_id": "knowledge-index-" + "a" * 32,
        },
    ]

    activated = service.activate_materialized_result(
        job_id="knowledge-index-" + "a" * 32,
        result=materialized,
        artifact_references=result["artifact_refs"],
        task=task,
    )

    assert activated["knowledge_index"]["status"] == "completed"
    assert index_repository.items["idx-1"].status == "completed"
    assert run_repository.items["run-1"].status == "completed"
    assert index_repository.items["idx-1"].index_metadata[
        "knowledge_index_execution_binding"
    ]["projection_state"] == "projected"


def test_worker_cannot_overwrite_foreign_index_or_run_ids(tmp_path) -> None:
    manifest = b'{"index_record_count":1}'
    index = b'{"file":"agent/runtime.py"}\n'
    refs = [
        reference(
            artifact_id="artifact-manifest",
            role="manifest",
            filename="manifest.json",
            content=manifest,
        ),
        reference(
            artifact_id="artifact-index",
            role="index",
            filename="index.jsonl",
            content=index,
        ),
    ]
    downloader = Downloader(
        {"artifact-manifest": manifest, "artifact-index": index}
    )
    index_repository = Repository()
    run_repository = Repository()
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=index_repository,
        knowledge_index_run_repository=run_repository,
        output_root=tmp_path,
    )
    first_task = assigned_task()
    service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=completed_result(refs),
        task=first_task,
    )
    index_before = index_repository.items["idx-1"].model_dump()
    run_before = run_repository.items["run-1"].model_dump()
    calls_before = list(downloader.calls)

    foreign_task = assigned_task()
    foreign_task["worker_execution_context"]["knowledge_index_job"][
        "job_id"
    ] = "knowledge-index-" + "b" * 32
    with pytest.raises(
        ValueError,
        match="materialization_binding_conflict",
    ):
        service.materialize(
            job_id="knowledge-index-" + "b" * 32,
            result=completed_result(refs),
            task=foreign_task,
        )

    assert index_repository.items["idx-1"].model_dump() == index_before
    assert run_repository.items["run-1"].model_dump() == run_before
    assert downloader.calls == calls_before


def test_same_binding_materialization_replay_is_idempotent(tmp_path) -> None:
    manifest = b'{"index_record_count":1}'
    index = b'{"file":"agent/runtime.py"}\n'
    refs = [
        reference(
            artifact_id="artifact-manifest",
            role="manifest",
            filename="manifest.json",
            content=manifest,
        ),
        reference(
            artifact_id="artifact-index",
            role="index",
            filename="index.jsonl",
            content=index,
        ),
    ]
    downloader = Downloader(
        {"artifact-manifest": manifest, "artifact-index": index}
    )
    service = KnowledgeIndexWorkerArtifactService(
        downloader=downloader,
        knowledge_index_repository=Repository(),
        knowledge_index_run_repository=Repository(),
        output_root=tmp_path,
    )
    task = assigned_task()

    first = service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=completed_result(refs),
        task=task,
    )
    first_downloads = list(downloader.calls)
    replay = service.materialize(
        job_id="knowledge-index-" + "a" * 32,
        result=completed_result(refs),
        task=task,
    )

    assert replay["knowledge_index"]["id"] == first[
        "knowledge_index"
    ]["id"]
    assert replay["run"]["id"] == first["run"]["id"]
    assert replay["run"]["knowledge_index_id"] == "idx-1"
    assert downloader.calls == first_downloads
