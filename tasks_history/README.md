# Task History

Each file in this directory is named after the corresponding agent role (e.g., `architect.json`).

Files contain a JSON array where each element records a single task:

```json
[
  { "task": "Description of work", "date": "2025-08-09T16:34:06Z" }
]
```

The AI agent appends new entries with the current timestamp to track completed tasks per role.
