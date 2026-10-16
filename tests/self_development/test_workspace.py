"""Publication takes only bounded exact regular-file changes."""
import hashlib

import pytest

from agent.services.self_development.workspace import fingerprint, seal_changes


def workspace(tmp_path):
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_original.py').write_text('before\n')
    return root, fingerprint(root, ['tests/test_original.py'])


def test_only_exact_declared_bytes_are_sealed(tmp_path):
    root, baseline = workspace(tmp_path)
    (root / 'tests/test_original.py').write_text('after\n')
    seal = tmp_path / 'private'
    hashes = seal_changes(root, baseline, ['tests/test_original.py'], seal)
    assert hashes == {'tests/test_original.py': hashlib.sha256(b'after\n').hexdigest()}
    (root / 'tests/test_original.py').write_text('later\n')
    assert (seal / 'tests/test_original.py').read_text() == 'after\n'


def test_out_of_scope_source_edit_is_rejected(tmp_path):
    root, baseline = workspace(tmp_path)
    (root / 'tests/unapproved.py').write_text('unapproved')
    with pytest.raises(ValueError, match='out_of_scope'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')


def test_failed_empty_change_set_never_seals(tmp_path):
    root, baseline = workspace(tmp_path)
    with pytest.raises(ValueError, match='empty_or_out_of_scope'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')


def test_deletion_requires_explicit_separate_policy(tmp_path):
    root, baseline = workspace(tmp_path)
    (root / 'tests/test_original.py').unlink()
    with pytest.raises(ValueError, match='deletion_requires'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')
