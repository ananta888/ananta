"""Small deterministic Pi protocol examples shared by parser/provider tests."""

import copy


def pi_events(*, workspace="/workspace", model="selected-model", provider="ananta", answer="A\u2028B"):
    user = {"role": "user", "content": [{"type": "text", "text": "Explain this code."}]}
    assistant = {
        "role": "assistant", "content": [{"type": "text", "text": answer}],
        "api": "openai-completions", "provider": provider, "model": model, "stopReason": "stop",
    }
    return [
        {"type": "session", "version": 3, "cwd": workspace},
        {"type": "agent_start"}, {"type": "turn_start"},
        {"type": "message_start", "message": {"role": "user"}},
        {"type": "message_end", "message": copy.deepcopy(user)},
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "not authoritative"}},
        {"type": "message_end", "message": copy.deepcopy(assistant)},
        {"type": "turn_end", "message": copy.deepcopy(assistant), "toolResults": []},
        {"type": "agent_end", "messages": [copy.deepcopy(user), copy.deepcopy(assistant)], "willRetry": False},
        {"type": "agent_settled"},
    ]
