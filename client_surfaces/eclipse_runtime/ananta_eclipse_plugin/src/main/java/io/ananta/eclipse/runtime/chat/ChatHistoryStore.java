package io.ananta.eclipse.runtime.chat;

import io.ananta.eclipse.runtime.security.TokenRedaction;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

public final class ChatHistoryStore {
    private final boolean sessionOnly;
    private final List<ChatRuntimeModel.ChatMessage> messages = new ArrayList<>();

    public ChatHistoryStore(boolean sessionOnly) {
        this.sessionOnly = sessionOnly;
    }

    public void append(ChatRuntimeModel.ChatMessage message) {
        if (message == null) {
            return;
        }
        messages.add(new ChatRuntimeModel.ChatMessage(
                message.role(),
                TokenRedaction.redactSensitiveText(Objects.toString(message.text(), "")),
                message.references()
        ));
    }

    public List<ChatRuntimeModel.ChatMessage> messages() {
        return List.copyOf(messages);
    }

    public boolean isSessionOnly() {
        return sessionOnly;
    }

    public void clear() {
        messages.clear();
    }
}
