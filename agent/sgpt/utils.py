import os
import platform
import shlex
import subprocess
from tempfile import NamedTemporaryFile
from typing import Any, Callable, TypeVar

import typer
from click import BadParameter, UsageError

from sgpt.__version__ import __version__
from .integration import bash_integration, zsh_integration, pwsh_integration

F = TypeVar("F", bound=Callable[[Any, str], None])


def get_edited_prompt() -> str:
    """
    Opens the user's default editor to let them
    input a prompt, and returns the edited text.

    :return: String prompt.
    """
    with NamedTemporaryFile(suffix=".txt", delete=False) as file:
        # Create file and store path.
        file_path = file.name
    editor = os.environ.get("EDITOR", "vim")
    editor_cmd = shlex.split(editor) if editor else ["vim"]
    subprocess.run(editor_cmd + [file_path], check=False)  # noqa: S603 - local editor invocation from user env
    # Read file when editor is closed.
    with open(file_path, "r", encoding="utf-8") as file:
        output = file.read()
    os.remove(file_path)
    if not output:
        raise BadParameter("Couldn't get valid PROMPT from $EDITOR")
    return output


def run_command(command: str) -> None:
    """
    Runs a command in the user's shell using agent safeguards.
    :param command: A shell command to run.
    """
    # SGPT-5: Use agent's safeguarded shell execution
    try:
        from agent.shell import get_shell

        shell = get_shell()
        # execution via agent shell safeguards
        shell.execute(command)
    except ImportError:
        # Fallback to original logic if agent.shell is not available (e.g. standalone sgpt)
        import platform

        if platform.system() == "Windows":
            is_powershell = len(os.getenv("PSModulePath", "").split(os.pathsep)) >= 3
            powershell_exe = os.path.join(
                os.environ.get("WINDIR", r"C:\Windows"),
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe",
            )
            cmd_exe = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "cmd.exe")
            if is_powershell:
                subprocess.run([powershell_exe, "-Command", command], check=False)  # noqa: S603
            else:
                subprocess.run([cmd_exe, "/c", command], check=False)  # noqa: S603
        else:
            shell_env = os.environ.get("SHELL", "/bin/sh")
            subprocess.run([shell_env, "-c", command], check=False)  # noqa: S603 - explicit user shell


def option_callback(func: F) -> Callable[[Any, str], None]:
    def wrapper(cls: Any, value: str) -> None:
        if not value:
            return
        func(cls, value)
        raise typer.Exit()

    return wrapper


@option_callback
def install_shell_integration(*_args: Any) -> None:
    """
    Installs shell integration. Currently supports ZSH, Bash and PowerShell.
    Allows user to get shell completions in terminal by using hotkey.
    Replaces current "buffer" of the shell with the completion.
    """

    def _install(path: str, integration: str, identifier: str) -> None:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = ""

        start_marker = f"# {identifier} start"
        end_marker = f"# {identifier} end"
        full_integration = f"\n{start_marker}\n{integration.strip()}\n{end_marker}\n"

        if start_marker in content and end_marker in content:
            typer.echo(f"Updating integration in {path}...")
            import re

            pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
            new_content = re.sub(pattern, full_integration.strip(), content, flags=re.DOTALL)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        else:
            typer.echo(f"Installing integration in {path}...")
            with open(path, "a", encoding="utf-8") as f:
                f.write(full_integration)

    if platform.system() == "Windows":
        powershell_exe = os.path.join(
            os.environ.get("WINDIR", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        result = subprocess.run(  # noqa: S603 - fixed absolute powershell path
            [powershell_exe, "-NoProfile", "-Command", "echo $PROFILE"],
            capture_output=True,
            text=True,
            check=False,
        )
        profile_path = result.stdout.strip()
        if not profile_path:
            raise UsageError("Could not find PowerShell profile path.")
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        _install(profile_path, pwsh_integration, "Shell-GPT integration PowerShell")
        typer.echo("Done! Restart your PowerShell to apply changes.")
        return

    shell = os.getenv("SHELL", "")
    if "zsh" in shell:
        _install(os.path.expanduser("~/.zshrc"), zsh_integration, "Shell-GPT integration ZSH")
    elif "bash" in shell:
        _install(os.path.expanduser("~/.bashrc"), bash_integration, "Shell-GPT integration BASH")
    else:
        raise UsageError("ShellGPT integrations only available for ZSH, Bash and PowerShell.")

    typer.echo("Done! Restart your shell to apply changes.")


@option_callback
def get_sgpt_version(*_args: Any) -> None:
    """
    Displays the current installed version of ShellGPT
    """
    typer.echo(f"ShellGPT {__version__}")
