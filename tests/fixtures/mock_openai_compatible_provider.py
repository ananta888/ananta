"""Mock OpenAI-compatible /v1/chat/completions provider for E2E tests — AFR-FINAL-T008.

Provides deterministic mocked responses for tool_calls and JSON schema modes
without requiring a real LLM server.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock


FIBONACCI_TOOL_CALLS_RESPONSE = {
    "id": "chatcmpl-mock-fib-001",
    "object": "chat.completion",
    "model": "mock-model",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-001",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "app.py",
                                "content": (
                                    "from flask import Flask\n"
                                    "app = Flask(__name__)\n\n"
                                    "@app.route('/fib/<int:n>')\n"
                                    "def fib(n):\n"
                                    "    a, b = 0, 1\n"
                                    "    for _ in range(n):\n"
                                    "        a, b = b, a + b\n"
                                    "    return str(a)\n\n"
                                    "if __name__ == '__main__':\n"
                                    "    app.run()\n"
                                ),
                            }),
                        },
                    },
                    {
                        "id": "call-002",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "requirements.txt",
                                "content": "flask>=2.0\n",
                            }),
                        },
                    },
                    {
                        "id": "call-003",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "README.md",
                                "content": "# Fibonacci API\nA simple Fibonacci REST API.\n",
                            }),
                        },
                    },
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 50, "completion_tokens": 200, "total_tokens": 250},
}

FIBONACCI_JSON_SCHEMA_RESPONSE = {
    "id": "chatcmpl-mock-fib-002",
    "object": "chat.completion",
    "model": "mock-model",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "command": "mkdir fibonacci-api && cd fibonacci-api && pip install flask",
                    "tool_calls": [],
                }),
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
}


def make_mock_invoke_with_tools(
    tool_calls: list[dict[str, Any]] | None = None,
) -> Mock:
    """Return a mock for ModelInvocationService.invoke_with_tools that returns tool_calls."""
    if tool_calls is None:
        # Default: Fibonacci tool calls
        tool_calls = [
            {"name": "write_file", "args": {"path": "app.py", "content": "# fib\n"}},
            {"name": "write_file", "args": {"path": "requirements.txt", "content": "flask\n"}},
            {"name": "write_file", "args": {"path": "README.md", "content": "# Fibonacci\n"}},
        ]
    mock = Mock(return_value={
        "tool_calls": tool_calls,
        "finish_reason": "tool_calls",
    })
    return mock


def make_mock_invoke_with_json_schema(command: str | None = None) -> Mock:
    """Return a mock for ModelInvocationService.invoke_with_json_schema."""
    if command is None:
        command = "mkdir fibonacci-api"
    mock = Mock(return_value=json.dumps({
        "command": command,
        "tool_calls": [],
    }))
    return mock


def make_mock_invoke(raw_text: str | None = None) -> Mock:
    """Return a mock for ModelInvocationService.invoke (plain text)."""
    if raw_text is None:
        raw_text = json.dumps({
            "tool_calls": [
                {"name": "write_file", "args": {"path": "app.py", "content": "# fib"}}
            ]
        })
    return Mock(return_value=raw_text)
