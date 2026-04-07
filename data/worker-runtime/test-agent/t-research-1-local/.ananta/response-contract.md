# Response Contract

Return exactly one JSON object and no Markdown.

Required rules:
- The first character must be '{' and the last character must be '}'.
- Set at least one of `command` or `tool_calls`.
- `reason` must stay short and technical.
- If no tool call is needed, provide one concrete shell command in `command`.

Expected shape:
```json
{
  "reason": "Short technical reason",
  "command": "optional shell command",
  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "value" } } ]
}
```
