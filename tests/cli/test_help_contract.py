"""Help-contract tests: every registered command path must support --help with exit 0.

Rules enforced here (matching global_cli_contract.help_contract):
- Every domain group returns exit 0 for `--help`.
- Every domain group's --help output is non-empty.
- Every domain group's --help output contains its subcommands.
- Leaf subcommands return exit 0 for `[group] [sub] --help`.
- Help works without a running hub, without config.json, without network.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from agent.cli.commands import DOMAIN_MODULES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _help(dispatch_fn, argv=None) -> tuple[int, str]:
    """Call dispatch_fn([..., '--help']), return (exit_code, combined_output)."""
    call_argv = list(argv or []) + ["--help"]
    out = io.StringIO()
    err = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = dispatch_fn(call_argv)
        rc = 0 if result is None else int(result)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    combined = out.getvalue() + err.getvalue()
    return rc, combined


# ---------------------------------------------------------------------------
# Group-level tests
# ---------------------------------------------------------------------------

DOMAIN_NAMES = sorted(DOMAIN_MODULES)


@pytest.mark.parametrize("domain", DOMAIN_NAMES)
def test_domain_help_exit_0(domain):
    """ananta <domain> --help must exit 0."""
    mod = DOMAIN_MODULES[domain]
    rc, output = _help(mod.dispatch)
    assert rc == 0, f"'ananta {domain} --help' exited {rc}. Output:\n{output}"


@pytest.mark.parametrize("domain", DOMAIN_NAMES)
def test_domain_help_nonempty(domain):
    """ananta <domain> --help must produce non-empty output."""
    mod = DOMAIN_MODULES[domain]
    _, output = _help(mod.dispatch)
    assert len(output.strip()) > 0, f"'ananta {domain} --help' produced no output"


@pytest.mark.parametrize("domain", DOMAIN_NAMES)
def test_domain_help_lists_subcommands(domain):
    """ananta <domain> --help must mention at least one subcommand."""
    mod = DOMAIN_MODULES[domain]
    _, output = _help(mod.dispatch)
    subcommands = getattr(mod, "SUBCOMMANDS", [])
    if not subcommands:
        pytest.skip(f"Module {domain} has no SUBCOMMANDS defined")
    found = any(sc in output for sc in subcommands)
    assert found, (
        f"'ananta {domain} --help' doesn't mention any of {subcommands}.\n"
        f"Output:\n{output}"
    )


# ---------------------------------------------------------------------------
# Leaf subcommand help tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAIN_NAMES)
def test_leaf_subcommand_help(domain):
    """Every leaf subcommand in SUBCOMMANDS must support --help with exit 0."""
    mod = DOMAIN_MODULES[domain]
    subcommands = getattr(mod, "SUBCOMMANDS", [])
    if not subcommands:
        pytest.skip(f"Module {domain} has no SUBCOMMANDS defined")

    failures = []
    for sub in subcommands:
        rc, output = _help(mod.dispatch, [sub])
        if rc != 0:
            failures.append(f"  ananta {domain} {sub} --help → exit {rc}")
        if not output.strip():
            failures.append(f"  ananta {domain} {sub} --help → empty output")

    if failures:
        pytest.fail(
            f"Subcommand help failures for '{domain}':\n" + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Main entrypoint help
# ---------------------------------------------------------------------------

def test_main_help_exit_0():
    """ananta --help must exit 0."""
    from agent.cli.main import main
    out = io.StringIO()
    err = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = main(["--help"])
        rc = 0 if result is None else int(result)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    assert rc == 0, f"ananta --help exited {rc}"


def test_main_help_mentions_domains():
    """ananta --help must mention core domain groups."""
    from agent.cli.main import main
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            main(["--help"])
    except SystemExit:
        pass
    text = out.getvalue()
    for expected in ("goal", "config", "dev", "task", "prompt"):
        assert expected in text, f"'ananta --help' missing mention of '{expected}'"


def test_main_help_no_hub_required():
    """ananta --help must work without a running hub (no network calls)."""
    import socket
    original_connect = socket.socket.connect

    def _block_connect(self, *args, **kwargs):
        raise AssertionError("ananta --help must not make network calls")

    socket.socket.connect = _block_connect
    try:
        from agent.cli.main import main
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                main(["--help"])
        except SystemExit:
            pass
    finally:
        socket.socket.connect = original_connect
