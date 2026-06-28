---
title: Ananta Kova Markdown Demo
author: Ananta
theme: ananta-default
aspectRatio: "16:9"
deckId: ananta-kova-demo
sourcePath: assets/sample-decks/ananta-kova-demo.md
---

# Ananta Markdown Slides

Plain Markdown source, deterministic slide splits, safe preview, and Hub-governed export.

---

## Hub Worker Flow

```mermaid
graph TD
  User[User Goal] --> Hub[Hub Control Plane]
  Hub --> Queue[Task Queue]
  Queue --> Worker[Worker Container]
  Worker --> CodeCompass[CodeCompass Context]
  Worker --> Artifact[Markdown Deck Artifact]
```

---

## Code Block Separator Safety

The parser ignores separators inside fenced code blocks.

```text
This is not a slide break:
---
Still the same code block.
```

---

## Deck Artifact Contract

- Source remains plain `.md`
- Rendered HTML is derived output
- Export outputs are separate artifacts
- Provenance links source hash, theme, renderer, and job

---

## Security Boundary

Raw scripts, event handlers, active embeds, and `javascript:` links are removed before preview.

Export stays a backend worker job because privileged rendering must pass Hub policy.
