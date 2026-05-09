"""Interactive TUI for reviewing, executing, and iterating on repair commands on the host."""
from __future__ import annotations

import re
import subprocess
import sys
import termios
import tty
from dataclasses import dataclass, field
from typing import Literal, Optional

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class RepairCommand:
    command: str
    source_task: str = ""
    approved: bool = False
    skipped: bool = False
    executed: bool = False
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""


Verdict = Literal["fixed", "retry", "abort"]


@dataclass
class RepairTuiResult:
    """Result of one TUI iteration."""
    executed: list[RepairCommand] = field(default_factory=list)
    verdict: Verdict = "abort"   # what the user decided after execution


# ── Command extraction ───────────────────────────────────────────────────────

_SCRIPT_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell|zsh|cmd)?\s*\n(.*?)```", re.DOTALL
)
_COMMAND_LINE_RE = re.compile(r"^command=(.+)$", re.MULTILINE)


def _extract_commands(task_title: str, output: str) -> list[RepairCommand]:
    cmds: list[RepairCommand] = []

    for block in _SCRIPT_BLOCK_RE.findall(output):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                cmds.append(RepairCommand(command=line, source_task=task_title))

    if cmds:
        return cmds

    for m in _COMMAND_LINE_RE.finditer(output):
        cmd = m.group(1).strip()
        if cmd:
            cmds.append(RepairCommand(command=cmd, source_task=task_title))

    return cmds


def extract_commands_from_outputs(outputs: list[tuple[str, str]]) -> list[RepairCommand]:
    seen: set[str] = set()
    result: list[RepairCommand] = []
    for task_title, output in outputs:
        for cmd in _extract_commands(task_title, output):
            if cmd.command not in seen:
                seen.add(cmd.command)
                result.append(cmd)
    return result


def build_retry_context(history: list[RepairTuiResult]) -> str:
    """Build a context string for the next planning iteration from past results."""
    if not history:
        return ""
    lines = ["VORHERIGE REPARATURVERSUCHE (bitte andere Ansaetze vorschlagen):"]
    for i, r in enumerate(history, 1):
        lines.append(f"\nVersuch {i}:")
        for cmd in r.executed:
            ec = cmd.exit_code if cmd.exit_code is not None else "?"
            out = (cmd.stdout or cmd.stderr or "")[:120].replace("\n", " ")
            lines.append(f"  - {cmd.command}  →  exit={ec}  {out}")
    return "\n".join(lines)


# ── Raw keyboard input ───────────────────────────────────────────────────────

def _getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            rest = sys.stdin.read(2)
            return ch + rest
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── TUI rendering helpers ────────────────────────────────────────────────────

def _header(console: Console, title: str, iteration: int, max_iterations: int) -> None:
    iter_tag = f"[dim]Versuch {iteration}/{max_iterations}[/dim]  " if max_iterations > 1 else ""
    console.print(
        Panel(
            f"[bold]Repair TUI[/bold]  {iter_tag}[dim]{title}[/dim]\n"
            "[dim]Commands werden direkt auf dem Host ausgeführt (außerhalb Docker)[/dim]",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def _render_review(
    console: Console,
    commands: list[RepairCommand],
    current: int,
    goal_title: str,
    iteration: int,
    max_iterations: int,
) -> None:
    console.clear()
    approved_count = sum(1 for c in commands if c.approved)
    cmd = commands[current]

    _header(console, goal_title, iteration, max_iterations)

    bar_width = 28
    filled = int(bar_width * (current + 1) / len(commands))
    bar = "█" * filled + "░" * (bar_width - filled)
    console.print(
        f"\n  [dim]{current + 1}/{len(commands)}[/dim]  [dim]{bar}[/dim]  "
        f"Freigegeben: [green]{approved_count}[/green]\n"
    )

    status_color = "green" if cmd.approved else ("yellow" if cmd.skipped else "white")
    status_label = "✓ Freigegeben" if cmd.approved else ("⊘ Übersprungen" if cmd.skipped else "ausstehend")
    console.print(
        Panel(
            f"[bold cyan]{cmd.command}[/bold cyan]\n\n"
            f"[dim]Task:[/dim]   {cmd.source_task}\n"
            f"[dim]Status:[/dim] [{status_color}]{status_label}[/{status_color}]",
            title=f"Befehl {current + 1}",
            box=box.SIMPLE_HEAVY,
            padding=(0, 1),
        )
    )
    console.print(
        "\n  [bold]a[/bold]/[bold]y[/bold] Freigeben  "
        "[bold]s[/bold]/[bold]n[/bold] Überspringen  "
        "[bold]←[/bold]/[bold]b[/bold] Zurück  "
        "[bold]Enter[/bold] Freigeben+Weiter  "
        "[bold]q[/bold] Abbrechen\n"
    )


def _render_execution_results(
    console: Console,
    executed: list[RepairCommand],
    iteration: int,
    max_iterations: int,
    goal_title: str,
) -> None:
    console.clear()
    _header(console, goal_title, iteration, max_iterations)
    console.print()

    for i, cmd in enumerate(executed, 1):
        ec = cmd.exit_code
        ok = ec == 0
        icon = "[green]✓[/green]" if ok else f"[red]✗ exit {ec}[/red]"
        console.print(f"  {icon}  [cyan]{cmd.command}[/cyan]")
        if cmd.stdout:
            for line in cmd.stdout.splitlines()[:6]:
                console.print(f"      [dim]{line}[/dim]")
        if not ok and cmd.stderr:
            console.print(f"      [red]{cmd.stderr[:150]}[/red]")

    console.print()


def _render_verification(
    console: Console,
    executed: list[RepairCommand],
    iteration: int,
    max_iterations: int,
    goal_title: str,
) -> Verdict:
    _render_execution_results(console, executed, iteration, max_iterations, goal_title)

    all_ok = all(c.exit_code == 0 for c in executed if c.executed)
    if all_ok:
        console.print("  [green]Alle Befehle erfolgreich.[/green]\n")
    else:
        failed = sum(1 for c in executed if (c.exit_code or 0) != 0)
        console.print(f"  [yellow]{failed} Befehl(e) fehlgeschlagen.[/yellow]\n")

    if iteration < max_iterations:
        console.print(
            "  [bold]y[/bold]/[bold]Enter[/bold] Problem behoben (fertig)  "
            "[bold]r[/bold] Nächster Versuch  "
            "[bold]q[/bold] Abbrechen\n"
        )
    else:
        console.print(
            "  [bold]y[/bold]/[bold]Enter[/bold] Problem behoben (fertig)  "
            "[bold]q[/bold] Abbrechen  "
            f"[dim](letzter Versuch {iteration}/{max_iterations})[/dim]\n"
        )

    while True:
        key = _getch()
        if key in ("y", "Y", "\r", "\n"):
            return "fixed"
        if key in ("r", "R") and iteration < max_iterations:
            return "retry"
        if key in ("q", "Q", "\x03", "\x04"):
            return "abort"


# ── Public API ───────────────────────────────────────────────────────────────

def run_repair_tui(
    outputs: list[tuple[str, str]],
    *,
    goal_title: str = "Repair",
    iteration: int = 1,
    max_iterations: int = 1,
) -> RepairTuiResult:
    """One iteration of the repair TUI: review → confirm → execute → verify.

    Returns a RepairTuiResult with the executed commands and the user's verdict.
    """
    console = Console()

    if not sys.stdin.isatty():
        console.print("[yellow]TUI benötigt ein interaktives Terminal.[/yellow]", file=sys.stderr)
        return RepairTuiResult(verdict="abort")

    commands = extract_commands_from_outputs(outputs)

    if not commands:
        console.clear()
        _header(console, goal_title, iteration, max_iterations)
        console.print("\n[yellow]Keine Shell-Befehle in den Task-Outputs gefunden.[/yellow]")
        console.print("[dim]Beliebige Taste zum Beenden.[/dim]")
        _getch()
        return RepairTuiResult(verdict="abort")

    # ── Phase 1: Review ──────────────────────────────────────────────
    current = 0
    while current < len(commands):
        _render_review(console, commands, current, goal_title, iteration, max_iterations)
        key = _getch()

        if key in ("a", "y", "A", "Y"):
            commands[current].approved = True
            commands[current].skipped = False
            current += 1
        elif key in ("s", "n", "S", "N", " "):
            commands[current].skipped = True
            commands[current].approved = False
            current += 1
        elif key in ("\r", "\n"):
            commands[current].approved = True
            commands[current].skipped = False
            current += 1
        elif key in ("\x1b[D", "b", "B"):
            current = max(0, current - 1)
        elif key in ("\x1b[C",):
            commands[current].skipped = True
            commands[current].approved = False
            current += 1
        elif key in ("q", "Q", "\x03", "\x04"):
            console.clear()
            console.print("[yellow]Abgebrochen.[/yellow]")
            return RepairTuiResult(verdict="abort")

    # ── Phase 2: Confirm ─────────────────────────────────────────────
    approved = [c for c in commands if c.approved]

    console.clear()
    _header(console, goal_title, iteration, max_iterations)

    if not approved:
        console.print("\n[yellow]Keine Befehle freigegeben. Nichts auszuführen.[/yellow]")
        console.print("[dim]Beliebige Taste zum Beenden.[/dim]")
        _getch()
        return RepairTuiResult(verdict="abort")

    table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    table.add_column("#", style="dim", width=3, no_wrap=True)
    table.add_column("Befehl", style="bold cyan")
    table.add_column("Task", style="dim", overflow="fold")
    for i, cmd in enumerate(approved, 1):
        table.add_row(str(i), cmd.command, cmd.source_task[:50])

    console.print(table)
    console.print(
        f"\n[bold]{len(approved)}[/bold] Befehl(e) werden auf dem "
        "[bold]Host-System[/bold] ausgeführt (nicht in Docker).\n"
    )
    console.print("  [bold]Enter[/bold]/[bold]y[/bold] Ausführen   [bold]q[/bold] Abbrechen\n")

    key = _getch()
    if key not in ("\r", "\n", "y", "Y"):
        console.print("[yellow]Abgebrochen.[/yellow]")
        return RepairTuiResult(verdict="abort")

    # ── Phase 3: Execute ─────────────────────────────────────────────
    console.clear()
    _header(console, goal_title, iteration, max_iterations)
    console.print()

    for i, cmd in enumerate(approved, 1):
        console.print(f"  [dim][{i}/{len(approved)}][/dim] [cyan]{cmd.command}[/cyan]")
        try:
            proc = subprocess.run(
                cmd.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            cmd.exit_code = proc.returncode
            cmd.stdout = proc.stdout.strip()
            cmd.stderr = proc.stderr.strip()
            cmd.executed = True

            if cmd.stdout:
                for line in cmd.stdout.splitlines()[:8]:
                    console.print(f"      [dim]{line}[/dim]")
            if proc.returncode == 0:
                console.print("      [green]✓[/green]")
            else:
                err = cmd.stderr[:200] if cmd.stderr else ""
                console.print(
                    f"      [red]✗ exit {proc.returncode}[/red]"
                    + (f" — {err}" if err else "")
                )
        except subprocess.TimeoutExpired:
            cmd.exit_code = -1
            console.print("      [red]✗ timeout (60s)[/red]")
        except Exception as exc:
            cmd.exit_code = -1
            console.print(f"      [red]✗ {exc}[/red]")

    console.print()

    # ── Phase 4: Verify ──────────────────────────────────────────────
    verdict = _render_verification(
        console,
        approved,
        iteration=iteration,
        max_iterations=max_iterations,
        goal_title=goal_title,
    )
    return RepairTuiResult(executed=approved, verdict=verdict)
