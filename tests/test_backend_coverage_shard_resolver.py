from scripts.resolve_backend_coverage_shards import parse_collection_output


def test_parse_collection_output_counts_verbose_pytest_tree_items() -> None:
    output = """
<Dir ananta>
  <Package tests>
    <Module test_alpha.py>
      <Function test_one>
      <Function test_parameterized[value]>
    <Dir e2e>
      <Module test_flow.py>
        <Class TestFlow>
          <Function test_happy_path>
"""

    assert parse_collection_output(output, repo_root="ananta") == {
        "tests/test_alpha.py": 2,
        "tests/e2e/test_flow.py": 1,
    }


def test_parse_collection_output_prefers_compact_node_ids() -> None:
    output = """
tests/test_alpha.py::test_one
tests/test_alpha.py::test_parameterized[value]
tests/e2e/test_flow.py::TestFlow::test_happy_path
3 tests collected in 0.10s
"""

    assert parse_collection_output(output, repo_root="ananta") == {
        "tests/test_alpha.py": 2,
        "tests/e2e/test_flow.py": 1,
    }
