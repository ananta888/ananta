"""Interactive TUI for reviewing, executing, and auto-retrying repair commands on the host.

Loop logic:
  plan → TUI approve → execute on host → check exit codes (automatic)
       → all OK: done
       → any failed: feed failure context back into next plan iteration
       → user can abort at any point with q
"""
from __future__ import annotations

import re
import subprocess
import sys
import termios
import tty
from dataclasses import dataclass, field
from typing import Literal, Optional

from rich import box
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

    @property
    def succeeded(self) -> bool:
        return self.executed and self.exit_code == 0

    @property
    def failed(self) -> bool:
        return self.executed and self.exit_code != 0


# Verdict is now purely derived from execution results — user only controls abort.
Verdict = Literal["fixed", "retry", "abort"]


@dataclass
class RepairTuiResult:
    executed: list[RepairCommand] = field(default_factory=list)
    verdict: Verdict = "abort"
    # Human-readable summary of what failed, fed into the next plan context.
    failure_summary: str = ""


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
    """Build context for the next planning iteration from execution history."""
    if not history:
        return ""
    lines = ["VORHERIGE REPARATURVERSUCHE (bitte andere Ansaetze vorschlagen):"]
    for i, r in enumerate(history, 1):
        lines.append(f"\nVersuch {i}:")
        for cmd in r.executed:
            ec = cmd.exit_code if cmd.exit_code is not None else "?"
            out = (cmd.stdout or cmd.stderr or "")[:120].replace("\n", " ")
            status = "OK" if cmd.succeeded else f"FEHLER exit={ec}"
            lines.append(f"  [{status}] {cmd.command}  {out}")
        if r.failure_summary:
            lines.append(f"  Zusammenfassung: {r.failure_summary}")
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


def _getch_nonblock(timeout_s: float = 3.0) -> str | None:
    """Read one keypress, return None if timeout_s passes with no input."""
    import select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout_s)
        if not ready:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            rest = sys.stdin.read(2)
            return ch + rest
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── TUI rendering ────────────────────────────────────────────────────────────

def _header(console: Console, title: str, iteration: int, max_iterations: int) -> None:
    iter_tag = f"[dim]Versuch {iteration}/{max_iterations}[/dim]  " if max_iterations > 1 else ""
    console.print(
        Panel(
            f"[bold]Repair TUI[/bold]  {iter_tag}[dim]{title}[/dim]\n"
            "[dim]Befehle laufen direkt auf dem Host (außerhalb Docker)[/dim]",
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


def _render_execution(
    console: Console,
    approved: list[RepairCommand],
    goal_title: str,
    iteration: int,
    max_iterations: int,
) -> None:
    """Execute all approved commands and fill in exit_code / stdout / stderr."""
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
            if cmd.succeeded:
                console.print("      [green]✓[/green]")
            else:
                err = (cmd.stderr or "")[:200]
                console.print(
                    f"      [red]✗ exit {cmd.exit_code}[/red]"
                    + (f" — {err}" if err else "")
                )
        except subprocess.TimeoutExpired:
            cmd.exit_code = -1
            cmd.stderr = "timeout after 60s"
            cmd.executed = True
            console.print("      [red]✗ timeout (60s)[/red]")
        except Exception as exc:
            cmd.exit_code = -1
            cmd.stderr = str(exc)
            cmd.executed = True
            console.print(f"      [red]✗ {exc}[/red]")

    console.print()


def _render_result_screen(
    console: Console,
    executed: list[RepairCommand],
    goal_title: str,
    iteration: int,
    max_iterations: int,
) -> Verdict:
    """Show execution summary. Verdict is auto-derived from exit codes.

    - All succeeded → "fixed" (user presses Enter or any non-q key to confirm)
    - Any failed + more iterations → "retry" (auto after 4s, q to abort)
    - Any failed + last iteration → "abort" after user confirms
    """
    console.clear()
    _header(console, goal_title, iteration, max_iterations)
    console.print()

    failed_cmds = [c for c in executed if c.failed]
    ok_cmds = [c for c in executed if c.succeeded]

    for cmd in executed:
        icon = "[green]✓[/green]" if cmd.succeeded else f"[red]✗ exit {cmd.exit_code}[/red]"
        console.print(f"  {icon}  [cyan]{cmd.command}[/cyan]")
        if cmd.stdout:
            for line in cmd.stdout.splitlines()[:4]:
                console.print(f"      [dim]{line}[/dim]")
        if cmd.failed and cmd.stderr:
            console.print(f"      [red]{cmd.stderr[:150]}[/red]")

    console.print()

    if not failed_cmds:
        # ── All succeeded ─────────────────────────────────────────────
        console.print("  [bold green]✓ Alle Befehle erfolgreich — Problem behoben.[/bold green]\n")
        console.print("  [dim]Beliebige Taste zum Beenden...[/dim]")
        _getch()
        return "fixed"

    # ── Some failed ───────────────────────────────────────────────────
    console.print(
        f"  [bold red]✗ {len(failed_cmds)} Befehl(e) fehlgeschlagen[/bold red]  "
        f"[dim]({len(ok_cmds)} erfolgreich)[/dim]\n"
    )

    if iteration >= max_iterations:
        console.print(
            f"  [yellow]Letzter Versuch ({iteration}/{max_iterations}) abgeschlossen.[/yellow]\n"
        )
        console.print("  [dim]Beliebige Taste zum Beenden...[/dim]")
        _getch()
        return "abort"

    # Auto-retry mit Abbruch-Option
    wait_s = 4
    console.print(
        f"  [yellow]Starte Versuch {iteration + 1}/{max_iterations} automatisch...[/yellow]  "
        f"[dim][q] Abbrechen[/dim]\n"
    )

    for remaining in range(wait_s, 0, -1):
        console.print(f"  [dim]{remaining}...[/dim]", end="\r")
        key = _getch_nonblock(timeout_s=1.0)
        if key is not None and key.lower() in ("q", "\x03", "\x04"):
            console.print()
            console.print("[yellow]Abgebrochen.[/yellow]")
            return "abort"

    console.print()
    return "retry"


def _build_failure_summary(executed: list[RepairCommand]) -> str:
    parts = []
    for cmd in executed:
        if cmd.failed:
            err = (cmd.stderr or cmd.stdout or "")[:200].replace("\n", " ")
            parts.append(f"{cmd.command} → exit={cmd.exit_code} {err}")
    return "; ".join(parts)


# ── Public API ───────────────────────────────────────────────────────────────

def run_repair_tui(
    outputs: list[tuple[str, str]],
    *,
    goal_title: str = "Repair",
    iteration: int = 1,
    max_iterations: int = 1,
) -> RepairTuiResult:
    """One TUI iteration: review → confirm → execute → auto-verdict from exit codes.

    Verdict:
      "fixed"  — all approved commands exited 0
      "retry"  — some failed, more iterations available
      "abort"  — user pressed q, or last iteration exhausted
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
        if iteration < max_iterations:
            console.print(
                f"  [dim]Starte Versuch {iteration + 1} automatisch... [q] Abbrechen[/dim]"
            )
            key = _getch_nonblock(3.0)
            if key and key.lower() in ("q", "\x03", "\x04"):
                return RepairTuiResult(verdict="abort")
            return RepairTuiResult(verdict="retry")
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
        console.print("\n[yellow]Keine Befehle freigegeben.[/yellow]")
        if iteration < max_iterations:
            console.print(
                f"  [dim]Starte Versuch {iteration + 1} ohne Ausführung... [q] Abbrechen[/dim]"
            )
            key = _getch_nonblock(3.0)
            if key and key.lower() in ("q", "\x03", "\x04"):
                return RepairTuiResult(verdict="abort")
            return RepairTuiResult(verdict="retry", executed=[])
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
        "[bold]Host-System[/bold] ausgeführt.\n"
    )
    console.print("  [bold]Enter[/bold]/[bold]y[/bold] Ausführen   [bold]q[/bold] Abbrechen\n")

    key = _getch()
    if key not in ("\r", "\n", "y", "Y"):
        console.print("[yellow]Abgebrochen.[/yellow]")
        return RepairTuiResult(verdict="abort")

    # ── Phase 3: Execute ─────────────────────────────────────────────
    _render_execution(console, approved, goal_title, iteration, max_iterations)

    # ── Phase 4: Auto-verdict from exit codes ─────────────────────────
    verdict = _render_result_screen(
        console, approved, goal_title, iteration, max_iterations
    )
    failure_summary = _build_failure_summary(approved) if verdict == "retry" else ""
    return RepairTuiResult(executed=approved, verdict=verdict, failure_summary=failure_summary)
