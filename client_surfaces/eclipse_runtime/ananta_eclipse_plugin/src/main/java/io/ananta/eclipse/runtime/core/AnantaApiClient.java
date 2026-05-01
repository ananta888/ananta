package io.ananta.eclipse.runtime.core;

import io.ananta.eclipse.runtime.security.TokenRedaction;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
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
        return request("GET", "/v1/ananta/capabilities", null);
    }

    public ClientResponse listTasks() {
        return request("GET", "/tasks", null);
    }

    public ClientResponse listArtifacts() {
        return request("GET", "/artifacts", null);
    }

    public ClientResponse listApprovals() {
        return request("GET", "/tasks?status=review_required", null);
    }

    public ClientResponse listAuditEvents() {
        return request("GET", "/api/system/audit-logs?limit=100", null);
    }

    public ClientResponse listAuditEvents(String severity, String eventType, String objectId) {
        List<String> params = new ArrayList<>();
        if (!Objects.toString(severity, "").isBlank()) {
            params.add("severity=" + encodeQueryValue(severity));
        }
        if (!Objects.toString(eventType, "").isBlank()) {
            params.add("type=" + encodeQueryValue(eventType));
        }
        if (!Objects.toString(objectId, "").isBlank()) {
            params.add("object=" + encodeQueryValue(objectId));
        }
        params.add(0, "limit=100");
        return request("GET", "/api/system/audit-logs?" + String.join("&", params), null);
    }

    public ClientResponse listRepairs() {
        return request("GET", "/tasks?status=failed", null);
    }

    public ClientResponse getTask(String taskId) {
        return request("GET", "/tasks/" + encodePathSegment(taskId), null);
    }

    public ClientResponse getArtifact(String artifactId) {
        return request("GET", "/artifacts/" + encodePathSegment(artifactId), null);
    }

    public ClientResponse getRepairSession(String sessionId) {
        return getTask(sessionId);
    }

    public ClientResponse approveApproval(String approvalId, String comment) {
        return request(
                "POST",
                "/tasks/" + encodePathSegment(approvalId) + "/review",
                buildReviewPayload("approve", comment)
        );
    }

    public ClientResponse rejectApproval(String approvalId, String comment) {
        return request(
                "POST",
                "/tasks/" + encodePathSegment(approvalId) + "/review",
                buildReviewPayload("reject", comment)
        );
    }

    public ClientResponse approveRepairStep(String repairSessionId, String stepId, String comment) {
        return request(
                "POST",
                "/tasks/" + encodePathSegment(repairSessionId) + "/review",
                buildReviewPayload("approve", appendStepComment(stepId, comment))
        );
    }

    public ClientResponse submitGoal(String goalText, String contextPayloadJson) {
        return submitGoal(goalText, contextPayloadJson, null, null, null);
    }

    public ClientResponse submitGoal(
            String goalText,
            String contextPayloadJson,
            String operationPreset,
            String commandId,
            String profileId
    ) {
        String safeGoal = Objects.toString(goalText, "").trim();
        String safeContext = ensureJsonObject(contextPayloadJson);
        StringBuilder body = new StringBuilder();
        body.append("{\"goal\":\"").append(escapeJson(safeGoal)).append("\",\"context\":").append(safeContext);
        appendOptionalString(body, "operation_preset", operationPreset);
        appendOptionalString(body, "command_id", commandId);
        appendOptionalString(body, "profile_id", profileId);
        body.append("}");
        return request("POST", "/goals", body.toString());
    }

    public ClientResponse analyzeContext(String contextPayloadJson) {
        return submitGoal("Analyze current workspace context", contextPayloadJson, "repository_understanding", "io.ananta.eclipse.command.analyze", null);
    }

    public ClientResponse reviewContext(String contextPayloadJson) {
        return submitGoal("Review selected code changes", contextPayloadJson, "change_review", "io.ananta.eclipse.command.review", null);
    }

    public ClientResponse patchPlan(String contextPayloadJson) {
        return submitGoal("Create patch plan for selected context", contextPayloadJson, "bugfix_planning", "io.ananta.eclipse.command.patch", null);
    }

    public ClientResponse createProjectNew(
            String goalText,
            String contextPayloadJson,
            String blueprintId,
            String workProfileId
    ) {
        return submitGoal(goalText, contextPayloadJson, "new_project", "io.ananta.eclipse.command.new_project", null);
    }

    public ClientResponse createProjectEvolve(
            String goalText,
            String contextPayloadJson,
            String blueprintId,
            String workProfileId
    ) {
        return submitGoal(goalText, contextPayloadJson, "project_evolution", "io.ananta.eclipse.command.evolve_project", null);
    }

    private static String wrapContextPayload(String contextPayloadJson) {
        return "{\"context\":" + ensureJsonObject(contextPayloadJson) + "}";
    }

    private static String buildReviewPayload(String action, String comment) {
        String normalizedComment = Objects.toString(comment, "").trim();
        StringBuilder body = new StringBuilder();
        body.append("{\"action\":\"").append(escapeJson(action)).append("\"");
        if (normalizedComment.isEmpty()) {
            body.append("}");
            return body.toString();
        }
        body.append(",\"comment\":\"").append(escapeJson(normalizedComment)).append("\"}");
        return body.toString();
    }

    private static String appendStepComment(String stepId, String comment) {
        String normalizedStep = Objects.toString(stepId, "").trim();
        String normalizedComment = Objects.toString(comment, "").trim();
        if (normalizedStep.isEmpty()) {
            return normalizedComment;
        }
        if (normalizedComment.isEmpty()) {
            return "repair_step=" + normalizedStep;
        }
        return normalizedComment + " repair_step=" + normalizedStep;
    }

    private static String ensureJsonObject(String payload) {
        String trimmed = Objects.toString(payload, "").trim();
        if (trimmed.isEmpty()) {
            return "{}";
        }
        if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
            return trimmed;
        }
        return "{\"value\":\"" + escapeJson(trimmed) + "\"}";
    }

    private static String encodePathSegment(String value) {
        String normalized = Objects.toString(value, "").trim();
        if (normalized.isBlank()) {
            throw new IllegalArgumentException("path segment must not be blank");
        }
        return URLEncoder.encode(normalized, StandardCharsets.UTF_8);
    }

    private static String encodeQueryValue(String value) {
        return URLEncoder.encode(Objects.toString(value, "").trim(), StandardCharsets.UTF_8);
    }

    private static void appendOptionalString(StringBuilder builder, String key, String value) {
        String normalized = Objects.toString(value, "").trim();
        if (normalized.isBlank()) {
            return;
        }
        builder.append(",\"").append(key).append("\":\"").append(escapeJson(normalized)).append("\"");
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
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            String error = TokenRedaction.redactSensitiveText(Objects.toString(exc.getMessage(), "request_interrupted"));
            return new ClientResponse(
                    false,
                    null,
                    DegradedState.BACKEND_UNREACHABLE,
                    null,
                    error,
                    true
            );
        } catch (IOException exc) {
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
