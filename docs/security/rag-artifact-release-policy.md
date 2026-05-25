# RAG Artifact Release Policy

## Ziel

RAG-/CodeCompass-Kontext darf nur Inhalte aus explizit freigegebenen Artefakten enthalten.

## Grundregeln

- Kontextaufbau ist grant-gebunden auf ArtifactVersion-Ebene.
- Ohne gueltigen Grant kein Chunk im Prompt-/RAG-Kontext.
- Entscheidung erfolgt deterministisch vor Prompt-Erzeugung.

## Klassifikationsgrenzen

- `local_only` darf nicht an Cloud-Worker/Remote-LLM released werden.
- `restricted`/`secret` brauchen explizite, zielgerichtete Freigaben.
- Fehlende Klassifikation fuehrt zu Default-Deny.

## Chunk-Metadaten

Jeder RAG-Chunk fuehrt minimal:

- `artifact_id`
- `version_id`
- `classification`
- `grant_id`
- `source_scope` (local|cloud|remote_llm)

Damit ist nachvollziehbar, warum ein Chunk in einem Kontext enthalten war.

## Pipeline-Punkt

- Pruefung passiert vor Retrieval-Merge und vor Prompt-Komposition.
- Nachgelagerte LLM-Filter ersetzen diese Freigabepruefung nicht.
