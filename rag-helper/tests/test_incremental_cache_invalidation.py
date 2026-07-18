from pathlib import Path

from rag_helper.application.incremental_cache import (
    invalidate_incremental_cache_paths,
    load_incremental_cache,
    save_incremental_cache,
)


def test_targeted_cache_invalidation_retains_unaffected_types(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    save_incremental_cache(
        cache_path,
        {
            "version": 2,
            "options_signature": "same-options",
            "files": {
                "README.md": {"manifest": {"ext": "md"}},
                "src/app.py": {"manifest": {"ext": "py"}},
            },
        },
    )

    result = invalidate_incremental_cache_paths(cache_path, ["README.md"])
    reloaded = load_incremental_cache(cache_path)

    assert result == {
        "strategy": "targeted_file_type_invalidation",
        "requested_path_count": 1,
        "invalidated_path_count": 1,
        "retained_path_count": 1,
        "invalidated_extensions": ["md"],
    }
    assert set(reloaded["files"]) == {"src/app.py"}
