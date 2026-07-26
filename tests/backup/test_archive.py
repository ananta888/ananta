from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.ananta_backup.archive import (
    BoundedTarInfo,
    GpgRecipient,
    OpenPgpArchive,
    SafeTarExtractor,
    TreePlan,
    _TarStreamValidator,
)
from scripts.ananta_backup.errors import BackupError


def test_strict_secret_inventory_rejects_symlinks(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "key").write_text("private", encoding="utf-8")
    (secrets / "alias").symlink_to("key")

    with pytest.raises(BackupError, match="must not contain symlinks"):
        TreePlan.capture("workflow secrets", secrets, "bind/secrets", strict_files=True)


def test_inventory_rejects_special_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    os.mkfifo(source / "pipe")

    with pytest.raises(BackupError, match="special file"):
        TreePlan.capture("workspace", source, "bind/workspace")


def test_safe_extractor_rejects_parent_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.tar"
    with tarfile.open(archive_path, "w") as archive:
        entry = tarfile.TarInfo("../outside")
        body = b"not allowed"
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(BackupError, match="Unsafe archive path"):
        SafeTarExtractor.extract_file(archive_path, target)

    assert not (tmp_path / "outside").exists()


def test_empty_tree_preserves_its_archive_root(tmp_path: Path) -> None:
    source = tmp_path / "empty-workspaces"
    source.mkdir()
    archive_path = tmp_path / "empty-workspaces.tar"
    with tarfile.open(archive_path, "w") as archive:
        TreePlan.capture(
            "project workspaces",
            source,
            "bind/project-workspaces",
        ).write_to(archive)

    restored = tmp_path / "restored"
    restored.mkdir()
    SafeTarExtractor.extract_file(archive_path, restored)

    assert (restored / "bind" / "project-workspaces").is_dir()
    assert not any((restored / "bind" / "project-workspaces").iterdir())


@pytest.mark.parametrize(
    "entry_type",
    [tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME],
)
def test_safe_extractor_rejects_oversized_tar_metadata_before_body(
    tmp_path: Path,
    entry_type: bytes,
) -> None:
    archive_path = tmp_path / "oversized-metadata.tar"
    metadata = tarfile.TarInfo("metadata")
    metadata.type = entry_type
    metadata.size = 1024 * 1024 + 1
    archive_path.write_bytes(
        metadata.tobuf(format=tarfile.PAX_FORMAT) + b"\0" * 1024
    )
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(BackupError, match="extended metadata"):
        SafeTarExtractor.extract_file(archive_path, destination)


def _pax_record(key: str, value: str) -> bytes:
    body = f"{key}={value}\n".encode()
    length = len(body) + 2
    while True:
        record = f"{length} ".encode() + body
        if len(record) == length:
            return record
        length = len(record)


@pytest.mark.parametrize("extension_chain", ["pax", "gnu", "mixed"])
def test_safe_extractor_rejects_recursive_tar_extension_chains(
    tmp_path: Path,
    extension_chain: str,
) -> None:
    archive_path = tmp_path / f"{extension_chain}-chain.tar"
    content = bytearray()
    for index in range(40):
        if extension_chain == "pax" or (
            extension_chain == "mixed" and index % 2 == 0
        ):
            entry_type = tarfile.XHDTYPE
            body = _pax_record("comment", str(index))
        else:
            entry_type = tarfile.GNUTYPE_LONGNAME
            body = b"payload.txt\0"
        extension = tarfile.TarInfo(f"extension-{index}")
        extension.type = entry_type
        extension.size = len(body)
        content.extend(extension.tobuf(format=tarfile.PAX_FORMAT))
        content.extend(body)
        content.extend(b"\0" * (-len(body) % tarfile.BLOCKSIZE))
    terminal = tarfile.TarInfo("payload.txt")
    content.extend(terminal.tobuf(format=tarfile.PAX_FORMAT))
    content.extend(b"\0" * (tarfile.BLOCKSIZE * 2))
    archive_path.write_bytes(content)
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(BackupError, match="extension header nesting"):
        SafeTarExtractor.extract_file(archive_path, destination)


def test_bounded_tarinfo_rejects_gnu_sparse_entries() -> None:
    metadata = BoundedTarInfo("sparse")
    metadata.type = tarfile.GNUTYPE_SPARSE

    with pytest.raises(tarfile.InvalidHeaderError, match="sparse"):
        metadata._proc_sparse(None)  # type: ignore[arg-type]


def test_bounded_tarinfo_replays_normal_pax_metadata_in_stream_mode() -> None:
    buffer = io.BytesIO()
    long_name = f"payload/{'a' * 140}.txt"
    with tarfile.open(
        fileobj=buffer,
        mode="w",
        format=tarfile.PAX_FORMAT,
    ) as archive:
        entry = tarfile.TarInfo(long_name)
        body = b"bounded"
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    buffer.seek(0)

    with tarfile.open(
        fileobj=buffer,
        mode="r|",
        tarinfo=BoundedTarInfo,
    ) as archive:
        members = list(archive)

    assert [member.name for member in members] == [long_name]


def test_bounded_tarinfo_allows_sparse_text_in_a_pax_value() -> None:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer,
        mode="w",
        format=tarfile.PAX_FORMAT,
    ) as archive:
        entry = tarfile.TarInfo("payload.txt")
        entry.pax_headers = {
            "comment": "literal GNU.sparse.map text is not a sparse key"
        }
        body = b"bounded"
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    buffer.seek(0)

    with tarfile.open(
        fileobj=buffer,
        mode="r|",
        tarinfo=BoundedTarInfo,
    ) as archive:
        members = list(archive)

    assert [member.name for member in members] == ["payload.txt"]


def test_bounded_tarinfo_rejects_a_gnu_sparse_pax_key(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "sparse-pax.tar"
    with tarfile.open(
        archive_path,
        mode="w",
        format=tarfile.PAX_FORMAT,
    ) as archive:
        entry = tarfile.TarInfo("payload.txt")
        entry.pax_headers = {"GNU.sparse.fake": "1"}
        body = b"bounded"
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(BackupError, match="sparse PAX metadata"):
        SafeTarExtractor.extract_file(archive_path, destination)


def test_tar_stream_validator_applies_limits_to_unselected_members() -> None:
    validator = _TarStreamValidator(
        max_entries=2,
        max_total_bytes=8,
        max_file_bytes=6,
    )
    first = tarfile.TarInfo("unselected.bin")
    first.size = 6
    second = tarfile.TarInfo("metadata.json")
    second.size = 3

    assert validator.validate(first) == Path("unselected.bin")
    with pytest.raises(BackupError, match="restore size limit"):
        validator.validate(second)


@pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("zstd") is None,
    reason="GPG and zstd are required for the streaming integration test",
)
def test_openpgp_archive_round_trip_uses_public_recipient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gpg_home = tmp_path / "gnupg"
    gpg_home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(gpg_home))
    identity = "Ananta Backup Test <backup-test@invalid.example>"
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            identity,
            "rsa2048",
            "encr",
            "1d",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--fingerprint", "--list-keys", identity],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout
    fingerprint = next(
        fields[9]
        for line in listing.splitlines()
        if (fields := line.split(":"))[0] == "fpr"
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("encrypted payload", encoding="utf-8")
    encrypted = tmp_path / "backup.tar.zst.gpg"
    archive = OpenPgpArchive()
    recipient = GpgRecipient(fingerprint)
    archive.verify_public_recipient(recipient)
    archive.encrypt_trees(
        [TreePlan.capture("payload", source, "payload")],
        recipient,
        encrypted,
    )

    restored = tmp_path / "restored"
    restored.mkdir()
    archive.decrypt_to_directory(encrypted, restored)

    assert (restored / "payload" / "payload.txt").read_text(
        encoding="utf-8"
    ) == "encrypted payload"
    subprocess.run(
        ["gpgconf", "--kill", "all"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
