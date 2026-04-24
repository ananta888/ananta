package io.ananta.eclipse.runtime.core;

import io.ananta.eclipse.runtime.security.TokenRedaction;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Objects;

public final class AnantaApiClient {
    private final ClientProfile profile;
    private final HttpClient httpClient;

    public AnantaApiClient(ClientProfile profile) {
        this(profile, HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(profile.getTimeoutSeconds())).build());
    }

    public AnantaApiClient(ClientProfile profile, HttpClient httpClient) {
        this.profile = Objects.requireNonNull(profile, "profile");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    public ClientResponse getHealth() {
        return request("GET", "/health", null);
    }

    public ClientResponse getCapabilities() {
        return request("GET", "/capabilities", null);
    }

    public ClientResponse listTasks() {
        return request("GET", "/tasks", null);
    }

    public ClientResponse listArtifacts() {
        return request("GET", "/artifacts", null);
    }

    public ClientResponse listApprovals() {
        return request("GET", "/approvals", null);
    }

    public ClientResponse listAuditEvents() {
        return request("GET", "/audit", null);
    }

    public ClientResponse listRepairs() {
        return request("GET", "/repairs", null);
    }

    public ClientResponse submitGoal(String goalText, String contextPayloadJson) {
        String safeGoal = Objects.toString(goalText, "").trim();
        String safeContext = Objects.toString(contextPayloadJson, "{}").trim();
        String body = "{\"goal_text\":\"" + escapeJson(safeGoal) + "\",\"context\":" + safeContext + "}";
        return request("POST", "/goals", body);
    }

    private ClientResponse request(String method, String path, String bodyJson) {
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(profile.getBaseUrl() + path))
                .timeout(Duration.ofSeconds(profile.getTimeoutSeconds()))
                .header("Accept", "application/json")
                .header("Content-Type", "application/json");

        if (profile.hasAuthToken()) {
            builder.header("Authorization", "Bearer " + profile.getAuthToken());
        }

        if ("POST".equals(method)) {
            builder.POST(HttpRequest.BodyPublishers.ofString(Objects.toString(bodyJson, "{}")));
        } else {
            builder.GET();
        }

        try {
            HttpResponse<String> response = httpClient.send(builder.build(), HttpResponse.BodyHandlers.ofString());
            int statusCode = response.statusCode();
            String rawBody = Objects.toString(response.body(), "");
            boolean parseError = !rawBody.isBlank() && !looksLikeJson(rawBody);
            DegradedState state = mapStatusToState(statusCode, parseError);
            boolean ok = state == DegradedState.HEALTHY;
            return new ClientResponse(
                    ok,
                    statusCode,
                    state,
                    rawBody,
                    ok ? null : "request_failed:" + state.name().toLowerCase(),
                    isRetriable(state)
            );
        } catch (IOException | InterruptedException exc) {
            String error = TokenRedaction.redactSensitiveText(Objects.toString(exc.getMessage(), "request_failed"));
            return new ClientResponse(
                    false,
                    null,
                    DegradedState.BACKEND_UNREACHABLE,
                    null,
                    error,
                    true
            );
        }
    }

    static DegradedState mapStatusToState(int statusCode, boolean parseError) {
        if (parseError) {
            return DegradedState.MALFORMED_RESPONSE;
        }
        if (statusCode >= 200 && statusCode < 300) {
            return DegradedState.HEALTHY;
        }
        if (statusCode == 401) {
            return DegradedState.AUTH_FAILED;
        }
        if (statusCode == 403) {
            return DegradedState.POLICY_DENIED;
        }
        if (statusCode == 422) {
            return DegradedState.CAPABILITY_MISSING;
        }
        if (statusCode >= 500) {
            return DegradedState.BACKEND_UNREACHABLE;
        }
        return DegradedState.UNKNOWN_ERROR;
    }

    static boolean isRetriable(DegradedState state) {
        return state == DegradedState.BACKEND_UNREACHABLE
                || state == DegradedState.MALFORMED_RESPONSE
                || state == DegradedState.UNKNOWN_ERROR;
    }

    private static boolean looksLikeJson(String rawBody) {
        String trimmed = rawBody.trim();
        return trimmed.startsWith("{") || trimmed.startsWith("[");
    }

    private static String escapeJson(String value) {
        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }
}
