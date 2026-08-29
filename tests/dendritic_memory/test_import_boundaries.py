from scripts.check_dendritic_memory_boundaries import main


def test_hub_and_serializer_import_boundaries() -> None:
    assert main() == 0
