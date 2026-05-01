package io.ananta.eclipse.runtime.chat;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

public final class ChatRuntimeModel {
    private final List<ChatMessage> messages = new ArrayList<>();

    public ChatMessage addMessage(String role, String text, List<String> references) {
        ChatMessage message = new ChatMessage(
                clean(role, 40),
                clean(text, 8000),
                references == null ? List.of() : references.stream().map(item -> clean(item, 300)).toList()
        );
        messages.add(message);
        return message;
    }

    public List<ChatMessage> messages() {
        return List.copyOf(messages);
    }

    public boolean hasTaskOrArtifactReferences() {
        return messages.stream().anyMatch(message -> !message.references().isEmpty());
    }

    private static String clean(String value, int maxChars) {
        String text = Objects.toString(value, "").trim();
        return text.substring(0, Math.min(maxChars, text.length()));
    }

    public record ChatMessage(String role, String text, List<String> references) {
        public ChatMessage {
            references = references == null ? List.of() : List.copyOf(references);
        }
    }
}
