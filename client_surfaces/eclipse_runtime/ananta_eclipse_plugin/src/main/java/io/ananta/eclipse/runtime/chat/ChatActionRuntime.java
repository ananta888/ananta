package io.ananta.eclipse.runtime.chat;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.Objects;

public final class ChatActionRuntime {
    private final AnantaApiClient apiClient;

    public ChatActionRuntime(AnantaApiClient apiClient) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
    }

    public ClientResponse convertToGoal(String chatSummary, String contextJson) {
        return apiClient.submitGoal(
                Objects.toString(chatSummary, "").trim(),
                contextJson,
                "chat_followup",
                "io.ananta.eclipse.command.chat_to_goal",
                null
        );
    }
}
