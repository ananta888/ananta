from scripts.check_dspy_import_boundaries import violations


def test_dspy_imports_and_executable_deserialization_stay_inside_adapter() -> None:
    assert violations() == []
