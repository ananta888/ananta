"""Repository research uses actual, bounded, revision-bound source bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.services.self_development.source_context import (
    SourceContextError,
    build_source_context,
)
from agent.services.self_development.workspace import git


def _checkout(root: Path, files: dict[str, str | bytes]) -> str:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    git(root, 'init')
    git(root, 'add', '--', *files)
    git(root, '-c', 'user.name=Source Context Test', '-c', 'user.email=test@example.invalid',
        'commit', '-m', 'test(autopilot): create isolated source context fixture')
    return git(root, 'rev-parse', 'HEAD')


def test_context_contains_actual_sources_and_related_test_after_large_catalog(tmp_path: Path) -> None:
    # Given a real checkout, with relevant tests beyond 500 unrelated filenames.
    files = {f'agent/noise_{index:04}.py': 'unrelated = True\n' for index in range(510)}
    files.update({
        'agent/services/memory.py': 'def retrieve(query):\n    return query.upper()\n',
        'tests/test_memory.py': 'from agent.services.memory import retrieve\n',
    })
    revision = _checkout(tmp_path, files)

    # When explicit source references and task relevance guide research.
    context = build_source_context(tmp_path, item={
        'title': 'Evaluate memory retrieval',
        'references': [{'path': 'agent/services/memory.py'}],
    }, repository=list(files), revision=revision)

    # Then actual source and related test reach the assignment with immutable bindings.
    assert [entry['path'] for entry in context['files']] == [
        'agent/services/memory.py', 'tests/test_memory.py']
    observed = context['files'][0]
    assert observed['content'] == files['agent/services/memory.py']
    assert observed['sha256'] == hashlib.sha256(
        b'def retrieve(query):\n    return query.upper()\n').hexdigest()
    assert observed['revision'] == context['revision'] == revision
    assert observed['truncated'] is False


def test_context_obeys_file_and_total_content_budgets(tmp_path: Path) -> None:
    # Given many relevant long sources.
    files = {f'agent/memory_{index}.py': 'memory = 1\n' * 1000 for index in range(12)}
    revision = _checkout(tmp_path, files)

    # When source context is built.
    context = build_source_context(tmp_path, item={'title': 'memory'},
                                   repository=list(files), revision=revision)

    # Then truncation is explicit and all assignment limits hold.
    assert len(context['files']) == 5
    assert sum(len(entry['content']) for entry in context['files']) == 20000
    assert all(len(entry['content']) <= 4000 and entry['truncated']
               for entry in context['files'])


def test_context_obeys_file_count_with_small_sources(tmp_path: Path) -> None:
    # Given twelve equally relevant small files.
    files = {f'agent/memory_{index:02}.py': 'memory = 1\n' for index in range(12)}
    revision = _checkout(tmp_path, files)

    # When the source bundle is built.
    context = build_source_context(tmp_path, item={'title': 'memory'},
                                   repository=list(files), revision=revision)

    # Then at most eight sources enter the assignment.
    assert len(context['files']) == 8


def test_context_rejects_traversal_protected_paths_symlinks_and_binary(tmp_path: Path) -> None:
    # Given tracked sources including content that cannot be delegated safely.
    files = {'agent/safe.py': 'safe = True\n', 'agent/link.py': 'original = True\n',
             'agent/blob.bin': b'\x00\xff', 'agent/secrets/key.py': 'private = True\n'}
    revision = _checkout(tmp_path, files)
    (tmp_path / 'agent/link.py').unlink()
    (tmp_path / 'agent/link.py').symlink_to(tmp_path / 'agent/safe.py')
    references = ['../outside.py', '/etc/passwd', 'agent/secrets/key.py',
                  'agent/link.py', 'agent/blob.bin', 'agent/missing.py', 'agent/safe.py']

    # When explicit references attempt to include them.
    context = build_source_context(tmp_path, item={'references': references},
                                   repository=list(files), revision=revision)

    # Then only safe, registered source bytes enter the assignment.
    assert [entry['path'] for entry in context['files']] == ['agent/safe.py']
    assert {entry['path'] for entry in context['unavailable']} == set(references[:-1])


def test_context_rejects_stale_revision(tmp_path: Path) -> None:
    # Given a checkout and a requested revision that does not match its HEAD.
    _checkout(tmp_path, {'agent/memory.py': 'memory = 1\n'})

    # When research is requested under the wrong revision.
    with pytest.raises(SourceContextError, match='source_context_revision_mismatch'):
        build_source_context(tmp_path, item={'title': 'memory'},
                             repository=['agent/memory.py'], revision='0' * 40)


def test_context_marks_dirty_content_unavailable(tmp_path: Path) -> None:
    # Given bytes changed since the assigned revision.
    revision = _checkout(tmp_path, {'agent/memory.py': 'memory = 1\n'})
    (tmp_path / 'agent/memory.py').write_text('memory = 2\n')

    # When research is bound to the original revision.
    context = build_source_context(tmp_path, item={'title': 'memory'},
                                   repository=['agent/memory.py'], revision=revision)

    # Then changed bytes are not misrepresented as observations of that revision.
    assert context['files'] == []
    assert context['unavailable'] == [{
        'path': 'agent/memory.py', 'reason': 'source_revision_content_mismatch'}]
