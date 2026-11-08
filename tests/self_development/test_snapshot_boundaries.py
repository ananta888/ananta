"""Boundary tests for snapshot source reading and sealing."""
import hashlib
import os
from pathlib import Path

import pytest

from agent.services.self_development.workspace import read_source, seal_changes


def _workspace(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_original.py').write_text('before\n')
    return root, {'tests/test_original.py': hashlib.sha256(b'before\n').hexdigest()}


def test_read_source_rejects_symlink_file(tmp_path: Path):
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    target = tmp_path / 'outside_target.py'
    target.write_text('secret\n')
    link = root / 'tests/link.py'
    os.symlink(target, link)
    with pytest.raises(ValueError, match='source_symlink_denied'):
        read_source(root, 'tests/link.py')


def test_seal_changes_rejects_symlink_directory(tmp_path: Path):
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_original.py').write_text('before\n')
    outside_dir = tmp_path / 'outside_dir'
    outside_dir.mkdir()
    (outside_dir / 'evil.py').write_text('bad\n')
    os.symlink(outside_dir, root / 'tests/evil_dir')
    baseline = {'tests/test_original.py': hashlib.sha256(b'before\n').hexdigest()}
    with pytest.raises(ValueError, match='source_symlink_denied'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')


def test_read_source_rejects_file_larger_than_limit(tmp_path: Path):
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    big = root / 'tests/big.py'
    big.write_bytes(b'x' * 1_000_001)
    with pytest.raises(ValueError, match='source_file_invalid'):
        read_source(root, 'tests/big.py')


def test_seal_changes_rejects_undeclared_newly_created_file(tmp_path: Path):
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_original.py').write_text('before\n')
    baseline = {'tests/test_original.py': hashlib.sha256(b'before\n').hexdigest()}
    (root / 'tests/test_original.py').write_text('after\n')
    (root / 'tests/undeclared.py').write_text('new\n')
    with pytest.raises(ValueError, match='out_of_scope'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')
