package io.ananta.eclipse.runtime.snake;

import io.ananta.eclipse.runtime.core.ClientProfile;

import java.util.Objects;

public record AnantaSnakeHubConnectionConfig(
        boolean enabled,
        String baseUrl,
        String authMode,
        String authToken,
        int timeoutSeconds
) {
    public AnantaSnakeHubConnectionConfig {
        if (enabled) {
            ClientProfile normalized = new ClientProfile(
                    "snake",
                    Objects.toString(baseUrl, "http://localhost:8080"),
                    Objects.toString(authMode, "session_token"),
                    "local",
                    Objects.toString(authToken, ""),
                    timeoutSeconds
            );
            baseUrl = normalized.getBaseUrl();
            authMode = normalized.getAuthMode();
            authToken = normalized.getAuthToken();
            timeoutSeconds = normalized.getTimeoutSeconds();
        } else {
            baseUrl = "";
            authMode = "none";
            authToken = "";
            timeoutSeconds = 15;
        }
    }

    public static AnantaSnakeHubConnectionConfig disabled() {
        return new AnantaSnakeHubConnectionConfig(false, "", "none", "", 15);
    }

    public static AnantaSnakeHubConnectionConfig fromProfile(ClientProfile profile, boolean enabled) {
        ClientProfile input = Objects.requireNonNull(profile, "profile");
        return new AnantaSnakeHubConnectionConfig(
                enabled,
                input.getBaseUrl(),
                input.getAuthMode(),
                input.getAuthToken(),
                input.getTimeoutSeconds()
        );
    }

    public ClientProfile toClientProfile() {
        return new ClientProfile("snake", baseUrl, authMode, "local", authToken, timeoutSeconds);
    }
}
