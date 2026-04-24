package io.ananta.eclipse.runtime.core;

import java.net.URI;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

public final class ClientProfile {
    private final String profileId;
    private final String baseUrl;
    private final String authMode;
    private final String environment;
    private final String authToken;
    private final int timeoutSeconds;

    public ClientProfile(
            String profileId,
            String baseUrl,
            String authMode,
            String environment,
            String authToken,
            int timeoutSeconds
    ) {
        this.profileId = clean(profileId, "default");
        this.baseUrl = normalizeBaseUrl(baseUrl);
        this.authMode = clean(authMode, "session_token").toLowerCase();
        this.environment = clean(environment, "local").toLowerCase();
        this.authToken = clean(authToken, "");
        this.timeoutSeconds = validateTimeout(timeoutSeconds);
    }

    private static String clean(String value, String fallback) {
        String candidate = Objects.toString(value, "").trim();
        return candidate.isEmpty() ? fallback : candidate;
    }

    private static String normalizeBaseUrl(String value) {
        String candidate = clean(value, "http://localhost:8080");
        if (!candidate.startsWith("http://") && !candidate.startsWith("https://")) {
            throw new IllegalArgumentException("baseUrl must start with http:// or https://");
        }
        URI parsed = URI.create(candidate);
        if (parsed.getHost() == null || parsed.getHost().isBlank()) {
            throw new IllegalArgumentException("baseUrl must contain a valid host");
        }
        while (candidate.endsWith("/")) {
            candidate = candidate.substring(0, candidate.length() - 1);
        }
        return candidate;
    }

    private static int validateTimeout(int timeoutSeconds) {
        if (timeoutSeconds <= 0) {
            throw new IllegalArgumentException("timeoutSeconds must be > 0");
        }
        return Math.min(timeoutSeconds, 60);
    }

    public String getProfileId() {
        return profileId;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public String getAuthMode() {
        return authMode;
    }

    public String getEnvironment() {
        return environment;
    }

    public String getAuthToken() {
        return authToken;
    }

    public int getTimeoutSeconds() {
        return timeoutSeconds;
    }

    public boolean hasAuthToken() {
        return authToken != null && !authToken.isBlank();
    }

    public Map<String, String> toPersistenceMap() {
        Map<String, String> persisted = new LinkedHashMap<>();
        persisted.put("profile_id", profileId);
        persisted.put("base_url", baseUrl);
        persisted.put("auth_mode", authMode);
        persisted.put("environment", environment);
        persisted.put("timeout_seconds", String.valueOf(timeoutSeconds));
        return persisted;
    }
}
