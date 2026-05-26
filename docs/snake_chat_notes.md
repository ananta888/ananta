# Snake-Modus: Chat, AI-Snake und Notizblock

## Übersicht

Im Snake-Modus gibt es rechts unten ein Chat-/Notes-Panel (ab Terminalgröße 120×32).
Das Panel zeigt den aktiven Channel und empfangene Nachrichten.

## Tastatur-Shortcuts

| Taste | Aktion |
|-------|--------|
| `c` | Chat-Fokus aktivieren (im Snake-Modus) |
| `Esc` | Chat-Fokus verlassen → zurück zur Snake-Steuerung |
| `Enter` | Nachricht senden (nur im Chat-Fokus) |
| `PageUp` / `PageDown` | Nachrichten-Scrollback |
| `Alt+Up` / `Alt+Down` | Einzelne Zeile scrollen |

## Kommandos

### Channel wechseln

```
:chat room        # Raumchat #room
:chat ai          # AI-Snake-Chat (tutor-ai)
:chat @<snake-id> # Direktnachricht an eine Snake
:notes            # Privater Notizblock (local-only)
:channels         # Liste aller Channels mit Unread-Zähler
:chat retry       # Fehlgeschlagene Nachrichten erneut senden
```

### Notizen

```
:notes                   # Notizblock öffnen
:notes find <text>       # Notizen durchsuchen
:notes pin <id>          # Notiz als gepinned markieren
:notes unpin <id>        # Pin entfernen
:notes delete <id>       # Notiz tombstone-löschen
```

### AI-Kontext

```
:ai context notes on     # Notes temporär an AI freigeben (protokolliert!)
:ai context notes off    # Freigabe aufheben
```

### Hilfe

```
:help chat    # Chat-Kommandos anzeigen
:help notes   # Notes-Kommandos + local-only Hinweis
```

## Chat-Prompt Anzeige

Im Chat-Fokus zeigt die Eingabezeile den aktiven Channel:

| Channel | Prompt |
|---------|--------|
| `room:main` | `#room>` |
| `ai:tutor` | `@ai>` |
| `direct:s-abc` | `@>` |
| `notes:self` | `notes>` |

## Wichtig: Notes sind standard local-only

Private Notizen werden **niemals** an den Hub, andere Snakes oder die AI gesendet.
Das Panel zeigt `NOTES local-only` im Header.

Die AI-Snake kann Notizen nur nutzen, wenn der Operator explizit `:ai context notes on` ausführt.
Diese Freigabe wird im AI-Chat-Channel protokolliert.

## Artefakt-Erklärchat (T04.03)

Wenn ein Artefakt selektiert ist, kann die AI-Snake dazu befragt werden:

```
:chat ai
@ai> was ist das?
```

Die AI antwortet mit Bezug auf das aktive Artefakt.
Der genutzte Kontext wird kompakt im Chat-Header angezeigt, z. B. `ctx: artifact+local`.

## Troubleshooting

**Hub offline:** Der Raumchat und Direktchat sind nicht verfügbar. Notes und AI-Chat
funktionieren trotzdem (lokal).

**Terminal zu klein:** Bei Breite < 100 erscheint nur ein kompakter Unread-Zähler.
Bei Höhe < 24 wird kein Chat-Panel gerendert. Unread-Nachrichten bleiben erhalten.

**Nachricht fehlgeschlagen:** Delivery-Status zeigt `[failed]` neben dem Sendernamen.
Mit `:chat retry` werden alle fehlgeschlagenen Nachrichten erneut gesendet.

**Policy blockiert:** Systemnachricht mit `* [system] policy deny: <action> → <reason>`.
