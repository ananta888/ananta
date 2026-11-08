# Encoder / Decoder / State / KV-Batch Source Code Map

## Scope
This map records where encoder, decoder, state-machine, and KV-batch logic lives for the speech reconciliation self-development task.

## Files
| Concern | Path | Notes |
|---|---|---|
| Speech reconciliation state machine (domain) | `agent/models/speech_reconciliation_state_machine.py` | Table-driven lifecycle over `ananta_contracts.speech_reconciliation_state`. |
| Service re-export | `agent/services/speech_reconciliation_state_machine.py` | Re-exports the domain classes for service-layer imports. |
| Video encoder profile | `worker/meet_media/video_encoder_profile.py` | NVENC/CPU options; fixed local working-set policy. |

## State transitions
- States and transitions are defined in `ananta_contracts.speech_reconciliation_state`.
- Authority is fenced to `hub`; non-hub callers raise `SpeechReconciliationStateError` with reason code `speech_reconciliation_hub_authority_required`.
- Terminal states reject late events (`speech_reconciliation_late_event`). Invalid transitions raise `speech_reconciliation_transition_invalid`.

## KV-batch / encoder notes
- Encoder options are fixed in `worker/meet_media/video_encoder_profile.py` to avoid host-wide VRAM isolation assumptions.
- Lookahead and B-frames are disabled to keep surface count bounded (`NVENC_SURFACES = 4`).