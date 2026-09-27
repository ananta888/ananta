from agent import tools_file


def test_file_read_returns_only_the_requested_lines(tmp_path, monkeypatch):
    target = tmp_path / "material.md"
    target.write_text("".join(f"line {n}\n" for n in range(1, 11)), encoding="utf-8")
    monkeypatch.setattr(tools_file, "_check_file_access", lambda path, operation="read": (True, ""))
    monkeypatch.setattr(tools_file, "_resolve_workspace_path", lambda path: path)
    monkeypatch.setattr("agent.common.audit.log_audit", lambda *args, **kwargs: None)

    ranged = tools_file.file_read_tool(path=str(target), start_line=3, end_line=5)
    whole = tools_file.file_read_tool(path=str(target))

    assert ranged["content"] == "line 3\nline 4\nline 5\n"
    assert whole["content"].count("\n") == 10
