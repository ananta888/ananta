import subprocess
import os
from typing import Any, Dict

from pydantic import BaseModel, Field

try:
    from agent.shell import get_shell
except ImportError:
    get_shell = None


class Function(BaseModel):
    """
    Executes a shell command and returns the output (result).
    """

    shell_command: str = Field(
        ...,
        example="ls -la",
        description="Shell command to execute.",
    )  # type: ignore

    @classmethod
    def execute(cls, shell_command: str) -> str:
        if get_shell:
            try:
                shell = get_shell()
                output, exit_code = shell.execute(shell_command)
                return f"Exit code: {exit_code}, Output:\n{output}"
            except Exception as e:
                return f"Error executing via PersistentShell: {e}"
        
        # Fallback to subprocess if PersistentShell is not available
        process = subprocess.Popen(
            shell_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        output, _ = process.communicate()
        exit_code = process.returncode
        return f"Exit code: {exit_code}, Output:\n{output.decode()}"

    @classmethod
    def openai_schema(cls) -> Dict[str, Any]:
        """Generate OpenAI function schema from Pydantic model."""
        schema = cls.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": "execute_shell_command",
                "description": cls.__doc__.strip() if cls.__doc__ else "",
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
            },
        }
