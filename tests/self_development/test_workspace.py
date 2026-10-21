"""Publication takes only bounded exact regular-file changes."""
import hashlib
import json

import pytest

from agent.services.self_development.workspace import (
    fingerprint,
    seal_changes,
    write_scope_manifest,
)


def workspace(tmp_path):
    root = tmp_path / 'worker'
    (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_original.py').write_text('before\n')
    return root, fingerprint(root, ['tests/test_original.py'])


def test_scope_manifest_declares_writable_files(tmp_path):
    write_scope_manifest(tmp_path, ['tests/test_original.py'])
    rows = json.loads((tmp_path / '.ananta' / 'materialization-manifest.json').read_text())
    assert rows == [{'workspace_path': 'tests/test_original.py',
                     'allowed_operations': ['write', 'patch', 'replace']}]


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


def test_declared_new_file_that_was_never_written_fails_closed(tmp_path):
    # Given one declared file changed while the other declared new file was never
    # created — the deliverable the task contract promised stayed incomplete.
    root, baseline = workspace(tmp_path)
    (root / 'tests/test_original.py').write_text('after\n')
    # Then the seal refuses instead of accepting the half-finished change set.
    with pytest.raises(ValueError, match='scope_incomplete'):
        seal_changes(
            root,
            baseline,
            ['tests/test_original.py', 'tests/test_new.py'],
            tmp_path / 'seal',
        )


def test_failed_empty_change_set_never_seals(tmp_path):
    root, baseline = workspace(tmp_path)
    with pytest.raises(ValueError, match='empty_or_out_of_scope'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')


def test_deletion_requires_explicit_separate_policy(tmp_path):
    root, baseline = workspace(tmp_path)
    (root / 'tests/test_original.py').unlink()
    with pytest.raises(ValueError, match='deletion_requires'):
        seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')


def test_nested_rag_helper_source_is_preserved_while_root_runtime_context_is_ignored(tmp_path):
    root, baseline = workspace(tmp_path)
    nested = root / 'rag-helper/rag_helper/application.py'
    nested.parent.mkdir(parents=True)
    nested.write_text('tracked = True\n')
    import hashlib
    baseline['rag-helper/rag_helper/application.py'] = hashlib.sha256(nested.read_bytes()).hexdigest()
    (root / 'rag_helper').mkdir()
    (root / 'rag_helper/research-context.json').write_text('{}')
    (root / 'tests/test_original.py').write_text('after\n')
    hashes = seal_changes(root, baseline, ['tests/test_original.py'], tmp_path / 'seal')
    assert set(hashes) == {'tests/test_original.py'}
