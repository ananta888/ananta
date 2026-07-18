from pathlib import Path

from rag_helper.application.file_scanner import collect_files


def test_collect_files_applies_injected_hub_policy_after_safe_file_filters(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "config.yml"
    denied = tmp_path / "compose.yml"
    allowed.write_text("feature: true\n", encoding="utf-8")
    denied.write_text("services: {}\n", encoding="utf-8")
    observed: list[str] = []

    def predicate(_path: Path, relative_path: str) -> bool:
        observed.append(relative_path)
        return relative_path != "compose.yml"

    files = collect_files(
        tmp_path,
        extensions={"yml"},
        excludes=set(),
        file_inclusion_predicate=predicate,
    )

    assert files == [allowed]
    assert sorted(observed) == ["compose.yml", "config.yml"]
