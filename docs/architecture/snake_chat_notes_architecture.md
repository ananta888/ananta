# Snake Chat + Notes – Architektur

## Module und Verantwortlichkeiten

| Modul | Verantwortung |
|-------|---------------|
| `chat_state.py` | Channel-Modell, ChatMessage-v1, Unread, Deduplication |
| `chat_policy.py` | Sicherheitsgrenzen, Sensitive-Data-Filter, Audit |
| `chat_transport.py` | Non-blocking Hub-Polling + Outbox-Queue mit Retry |
| `snake_notes.py` | JSONL-Persistenz für Notes (local-only) |
| `ai_snake_context.py` | AI-Kontext-Modell, Notes-Release, Artefakt-Ref |
| `renderer.py` | Chat-Panel-Rendering im Snake-Split-View |
| `interactive.py` | Tastatur-Routing, Chat-Focus, Notes-Ops im Tick |
| `commands.py` | `:chat`, `:notes`, `:channels`, `:ai context` |
| `agent/routes/snakes.py` | Hub-API: ChatMessage-v1, Cursor-Polling, ACK |

## Nachricht senden (input → policy → state → transport → render)

```mermaid
flowchart TD
    A[Enter im Chat-Fokus] --> B[_chat_send_message]
    B --> C{channel_type?}
    C -->|notes| D[append_note JSONL]
    D --> E[append_message local]
    C -->|ai| F[tutor_ask_question setzen]
    F --> G[ai_typing=true]
    G --> H[Tick: poll AI answer]
    H --> I[append AI reply to ai:tutor]
    C -->|room/direct| J[check_policy]
    J -->|deny| K[system_message + delivery=blocked]
    J -->|allow| L[enqueue in ChatTransport]
    L --> M[Transport Thread: send to Hub]
    M --> N[delivery_state: sent/failed]
    E --> R[renderer._overlay_snake_chat_panel]
    I --> R
    K --> R
    N --> R
```

## AI-Kontextfreigabe (Notes → AI)

```mermaid
flowchart TD
    A[:ai context notes on] --> B[release_notes_context ctx=True]
    B --> C[allowed_context_refs += notes]
    B --> D[chat: system msg protokolliert]
    D --> E[AI-Frage via :chat ai]
    E --> F[build_context_payload]
    F --> G{notes_released?}
    G -->|yes| H[notes_context in payload]
    G -->|no| I[notes NICHT in payload]
    H --> J{is_external_ai?}
    J -->|yes| K[policy deny: external_ai_notes_denied]
    J -->|no| L[AI erhält Notes-Kontext]
```

## Notes local-only Boundary

```mermaid
flowchart TD
    A[User tippt in notes> Prompt] --> B[_chat_send_message]
    B --> C{channel_type == notes}
    C --> D[append_note → ~/.config/ananta/snake_notes.jsonl]
    D --> E[append_message local channel]
    E --> F[Chat-Panel zeigt NOTES local-only]
    B -.->|NIEMALS| G[ChatTransport.enqueue]
    B -.->|NIEMALS| H[Hub API POST /chat/messages]

    P[check_policy msg notes] --> Q[send_hub → deny: notes_local_only]
    P --> R[send_ai → deny: notes_context_not_released]
    P --> S[write_local → allow]
```

## Testmatrix

| Bereich | Datei | Tests |
|---------|-------|-------|
| Channel-Modell | `test_tui_snake_chat_state.py` | 32 Tests |
| Policy-Grenzen | `test_tui_snake_chat_policy.py` | 20 Tests |
| Hub-API Chat | `test_snakes_chat_api.py` | 14 Tests |
| E2E-Cast | `scripts/e2e/snake_chat_notes_e2e.py` | 70s, 7 Kapitel |

## Wichtige Invarianten

1. `channel_type == "notes"` → `visibility == "local_only"` → niemals in Transport-Queue
2. Notes-Nachrichten werden nur von `check_policy(msg, "write_local")` als `allow` bewertet
3. `is_external_ai=True` + notes → `deny: external_ai_notes_denied` (unabhängig von release)
4. Sensitive Patterns (tokens, passwords) werden am Boundary blockiert oder redacted
5. Audit-Events enthalten keinen vollständigen Nachrichtentext (nur `message_hash`)
6. Transport-Thread ist daemonized und blockiert weder Rendering noch Snake-Bewegung
