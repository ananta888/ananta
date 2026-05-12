"""ModelInvocationService stub — FA-T009 for tool calling/json schema LLM calls."""

class ModelInvocationService:
    """Stub for LLM invocation with tools/json_schema."""

    @classmethod
    def invoke_with_tools(cls, prompt: str, tools: list, model: str, **kwargs) -> dict:
        '''Invoke LLM with tools, return response dict.'''
        # TODO: Real implementation with openai/anthropic clients
        return {
            "tool_calls": [
                {
                    "name": "write_file",
                    "args": {"path": "main.py", "content": "# Stub LLM tool call"}
                }
            ]
        }

    @classmethod
    def invoke_with_json_schema(cls, prompt: str, json_schema: dict, model: str, **kwargs) -> str:
        '''Invoke with response_format json_schema, return raw response content.'''
        # TODO: Real
        import json
        return json.dumps({
            "tool_calls": [
                {
                    "name": "write_file",
                    "args": {"path": "main.py", "content": "# Stub JSON schema"}
                }
            ]
        })

    @classmethod
    def invoke(cls, prompt: str, model: str, **kwargs) -> str:
        '''Plain LLM invoke, return content string.'''
        # TODO: Real
        return '{"tool_calls": [{"name": "write_file", "args": {"path": "main.py"}}]}'  # valid for repair stub
