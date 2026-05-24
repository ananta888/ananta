from agent.services.command_chain_parser import CommandChainParser


def test_parse_and_chain_into_three_segments():
    plan = CommandChainParser().parse("pytest && git status && git diff")
    assert plan.allowed is True
    assert [s.raw for s in plan.segments] == ["pytest", "git status", "git diff"]
    assert [s.operator_before for s in plan.segments] == [None, "&&", "&&"]


def test_parse_or_and_semicolon():
    or_plan = CommandChainParser().parse("pytest || echo failed")
    assert or_plan.allowed is True
    assert [s.raw for s in or_plan.segments] == ["pytest", "echo failed"]
    assert [s.operator_before for s in or_plan.segments] == [None, "||"]

    seq_plan = CommandChainParser().parse("pytest; git status")
    assert seq_plan.allowed is True
    assert [s.raw for s in seq_plan.segments] == ["pytest", "git status"]
    assert [s.operator_before for s in seq_plan.segments] == [None, ";"]


def test_parse_keeps_quoted_chain_tokens_literal():
    plan = CommandChainParser().parse('echo "a && b" && pytest')
    assert plan.allowed is True
    assert [s.raw for s in plan.segments] == ['echo "a && b"', "pytest"]


def test_parse_rejects_pipe_but_allows_redirect():
    pipe_plan = CommandChainParser().parse("cat a | grep x")
    assert pipe_plan.allowed is False
    assert "|" in pipe_plan.unsupported_operators

    redirect_plan = CommandChainParser().parse("echo x > file.txt")
    assert redirect_plan.allowed is True
    assert redirect_plan.unsupported_operators == []


# SCG-003: OpenCode regression — the concrete failing command from the bug report


def test_parse_opencode_python_semicolon_chain():
    """python hello.py; python -c "..." must split into exactly two segments."""
    cmd = 'python hello.py; python -c "from hello import greet; print(greet(\'World\'))"'
    plan = CommandChainParser().parse(cmd)
    assert plan.allowed is True
    assert len(plan.segments) == 2
    assert plan.segments[0].raw == "python hello.py"
    assert plan.segments[1].raw.startswith('python -c "from hello import greet')
    # The second segment must contain the Python-level semicolon intact
    assert ";" in plan.segments[1].raw
    assert plan.segments[1].operator_before == ";"


def test_parse_python_c_quoted_semicolon_literal():
    """Semicolons inside double quotes are NOT shell operators."""
    cmd = 'python -c "a = 1; b = 2; print(a + b)"'
    plan = CommandChainParser().parse(cmd)
    assert plan.allowed is True
    assert len(plan.segments) == 1
    assert ";" in plan.segments[0].raw  # literal, not a shell split


def test_parse_python_c_single_quote_semicolon_literal():
    """Semicolons inside single quotes are also NOT shell operators."""
    cmd = "python -c 'import sys; sys.exit(0)'"
    plan = CommandChainParser().parse(cmd)
    assert plan.allowed is True
    assert len(plan.segments) == 1
