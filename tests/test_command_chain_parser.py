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


def test_parse_rejects_pipe_and_redirect():
    pipe_plan = CommandChainParser().parse("cat a | grep x")
    assert pipe_plan.allowed is False
    assert "|" in pipe_plan.unsupported_operators

    redirect_plan = CommandChainParser().parse("echo x > file.txt")
    assert redirect_plan.allowed is False
    assert ">" in redirect_plan.unsupported_operators

