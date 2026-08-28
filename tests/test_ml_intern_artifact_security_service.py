from __future__ import annotations

import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityError,
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)


def _safetensors_bytes(payload: bytes = b"\x00\x00\x00\x00") -> bytes:
    header = json.dumps(
        {"lora.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, len(payload)]}},
        separators=(",", ":"),
    ).encode("utf-8")
    return len(header).to_bytes(8, "little") + header + payload


def _service(tmp_path: Path, **overrides) -> MlInternArtifactSecurityService:
    values = {
        "max_file_bytes": 1024 * 1024,
        "max_request_bytes": 2 * 1024 * 1024,
        "max_tenant_bytes": 4 * 1024 * 1024,
        "max_archive_uncompressed_bytes": 2 * 1024 * 1024,
        "max_compression_ratio": 100.0,
    }
    values.update(overrides)
    return MlInternArtifactSecurityService(
        storage_root=tmp_path / "store",
        policy=ArtifactSecurityPolicy(**values),
    )


def test_containment_blocks_absolute_traversal_prefix_collision_and_symlink(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for unsafe in ("/etc/passwd", "../store-other/file", "a/../../outside", "C:\\temp\\x"):
        with pytest.raises(ArtifactSecurityError) as exc:
            service.resolve_relative(unsafe)
        assert exc.value.reason_code in {"path_escape", "unsafe_path"}

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "store" / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactSecurityError, match="symlink"):
        service.resolve_relative("linked/file.json")


def test_stream_upload_is_bounded_hashed_atomic_and_cleans_up(tmp_path: Path) -> None:
    service = _service(tmp_path, max_file_bytes=16, max_request_bytes=32, max_tenant_bytes=64)
    stored = service.store_upload(
        io.BytesIO(b'{"x":1}\n'),
        destination_relative="tenant/upload.jsonl",
        filename="upload.jsonl",
        media_type="application/x-ndjson",
        allowed_extensions={".jsonl"},
        allowed_media_types={"application/x-ndjson"},
        content_kind="jsonl",
    )
    assert stored.size_bytes == 8
    assert len(stored.sha256) == 64
    assert service.resolve_relative(stored.relative_path, must_exist=True).read_bytes() == b'{"x":1}\n'

    with pytest.raises(ArtifactSecurityError) as exc:
        service.store_upload(
            io.BytesIO(b"x" * 17),
            destination_relative="tenant/too-large.bin",
            filename="too-large.bin",
        )
    assert exc.value.reason_code == "file_quota_exceeded"
    assert not service.resolve_relative("tenant/too-large.bin").exists()
    assert not list(service.resolve_relative("tenant").glob(".upload-*"))

    with pytest.raises(ArtifactSecurityError) as exc:
        service.store_upload(
            io.BytesIO(b'{"x":1}'),
            destination_relative="tenant/cancel.json",
            filename="cancel.json",
            cancel_check=lambda: True,
        )
    assert exc.value.reason_code == "upload_cancelled"
    assert not service.resolve_relative("tenant/cancel.json").exists()


def test_upload_rejects_extension_media_and_content_mismatch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ArtifactSecurityError) as exc:
        service.store_upload(
            io.BytesIO(b"not-json"),
            destination_relative="x/data.json",
            filename="data.json",
            media_type="text/plain",
            allowed_extensions={".json"},
            allowed_media_types={"application/json"},
            content_kind="json",
        )
    assert exc.value.reason_code == "media_type_not_allowed"

    with pytest.raises(ArtifactSecurityError) as exc:
        service.store_upload(
            io.BytesIO(b"not-a-zip"),
            destination_relative="x/fake.zip",
            filename="fake.zip",
            content_kind="zip",
        )
    assert exc.value.reason_code == "content_type_mismatch"


def test_needle_adapter_is_hashed_as_opaque_single_file(tmp_path: Path) -> None:
    service = _service(tmp_path)
    root = service.resolve_relative("needle")
    root.mkdir()
    (root / "adapter.pkl").write_bytes(b"opaque-worker-output")

    inspected = service.validate_needle_adapter_tree(root)

    assert inspected["files"][0]["name"] == "adapter.pkl"
    assert len(inspected["tree_sha256"]) == 64

    (root / "unexpected.json").write_text("{}")
    with pytest.raises(ArtifactSecurityError, match="only adapter.pkl"):
        service.validate_needle_adapter_tree(root)


def _store_zip(service: MlInternArtifactSecurityService, payload: bytes, name: str = "adapter.zip") -> Path:
    stored = service.store_upload(
        io.BytesIO(payload),
        destination_relative=f"archives/{name}",
        filename=name,
        content_kind="zip",
    )
    return service.resolve_relative(stored.relative_path, must_exist=True)


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return output.getvalue()


def test_archive_extracts_valid_zip_and_blocks_zip_slip_duplicates_and_nested(tmp_path: Path) -> None:
    service = _service(tmp_path)
    valid = _store_zip(service, _zip_bytes([("adapter_config.json", b"{}"), ("adapter_model.safetensors", b"safe")]))
    result = service.extract_archive(valid, destination_relative="out/valid")
    assert result.member_count == 2
    assert service.resolve_relative("out/valid/adapter_config.json", must_exist=True).read_bytes() == b"{}"

    attacks = {
        "slip.zip": [("../escape", b"x")],
        "nested.zip": [("payload.zip", b"x")],
        "duplicate.zip": [("same.json", b"a"), ("same.json", b"b")],
    }
    expected = {
        "slip.zip": "archive_path_escape",
        "nested.zip": "nested_archive_forbidden",
        "duplicate.zip": "archive_duplicate_member",
    }
    for name, entries in attacks.items():
        archive = _store_zip(service, _zip_bytes(entries), name=name)
        with pytest.raises(ArtifactSecurityError) as exc:
            service.extract_archive(archive, destination_relative=f"out/{name}")
        assert exc.value.reason_code == expected[name]
        assert not service.resolve_relative(f"out/{name}").exists()


def test_archive_blocks_zip_symlink_tar_link_and_compression_bomb(tmp_path: Path) -> None:
    service = _service(tmp_path)
    symlink_zip = io.BytesIO()
    with zipfile.ZipFile(symlink_zip, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    path = _store_zip(service, symlink_zip.getvalue(), name="link.zip")
    with pytest.raises(ArtifactSecurityError) as exc:
        service.extract_archive(path, destination_relative="out/link")
    assert exc.value.reason_code == "archive_link_forbidden"

    tar_payload = io.BytesIO()
    with tarfile.open(fileobj=tar_payload, mode="w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)
    tar_stored = service.store_upload(
        io.BytesIO(tar_payload.getvalue()),
        destination_relative="archives/link.tar",
        filename="link.tar",
        content_kind="tar",
    )
    with pytest.raises(ArtifactSecurityError) as exc:
        service.extract_archive(
            service.resolve_relative(tar_stored.relative_path, must_exist=True),
            destination_relative="out/tar-link",
        )
    assert exc.value.reason_code == "archive_link_forbidden"

    strict = _service(tmp_path / "strict", max_compression_ratio=2.0)
    bomb = _store_zip(strict, _zip_bytes([("huge.txt", b"0" * 100_000)]), name="bomb.zip")
    with pytest.raises(ArtifactSecurityError) as exc:
        strict.extract_archive(bomb, destination_relative="out/bomb")
    assert exc.value.reason_code == "archive_compression_bomb"


def test_safetensors_header_and_offsets_are_checked_without_loading_weights(tmp_path: Path) -> None:
    service = _service(tmp_path)
    stored = service.store_upload(
        io.BytesIO(_safetensors_bytes()),
        destination_relative="adapter/adapter_model.safetensors",
        filename="adapter_model.safetensors",
    )
    report = service.inspect_safetensors(service.resolve_relative(stored.relative_path, must_exist=True))
    assert report == {"tensor_count": 1, "header_bytes": report["header_bytes"], "data_bytes": 4}

    broken = service.store_upload(
        io.BytesIO((9999).to_bytes(8, "little") + b"{}"),
        destination_relative="adapter/broken.safetensors",
        filename="broken.safetensors",
    )
    with pytest.raises(ArtifactSecurityError) as exc:
        service.inspect_safetensors(service.resolve_relative(broken.relative_path, must_exist=True))
    assert exc.value.reason_code == "invalid_safetensors_header"
