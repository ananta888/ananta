# Mermaid Fixture

```mermaid
flowchart TD
  A[Start] --> B{Condition}
  B -->|Yes| C[Action]
  B -->|No| D[Fallback]
  C --> E[Done]
  D --> E
```
