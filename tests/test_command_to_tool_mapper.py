from agent.services.command_to_tool_mapper import CommandToToolMapper


def test_maps_common_safe_commands():
    mapper = CommandToToolMapper()
    assert mapper.map("pytest").mapped_tool == "run_tests"
    assert mapper.map("git status").mapped_tool == "git_status"
    assert mapper.map("git diff").mapped_tool == "git_diff"
    assert mapper.map("cat app.py").mapped_tool == "file_read"


def test_does_not_map_redirect_or_pipe():
    mapper = CommandToToolMapper()
    assert mapper.map("echo x > app.py").mapped_tool is None
    assert mapper.map("cat a | grep b").mapped_tool is None

