package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

public final class EclipsePolicyFallbackUx {
    public PolicyDeniedModel buildPolicyDenied(
            String actionId,
            String denialReason,
            String traceId,
            String baseUrl
    ) {
        return new PolicyDeniedModel(
                Objects.toString(actionId, "").trim(),
                Objects.toString(denialReason, "").trim(),
                Objects.toString(traceId, "").trim(),
                browserFallbackLinks(baseUrl, null, null, null, traceId),
                List.of(
                        "Open governance detail in browser for policy context.",
                        "Request explicit permission if capability is missing.",
                        "Retry action only after backend policy change."
                ),
                true
        );
    }

    public PolicyDeniedModel fromResponse(String actionId, ClientResponse response, String baseUrl) {
        String denialReason = response == null ? "policy_denied" : Objects.toString(response.getError(), "policy_denied");
        String traceId = extractTraceHint(response);
        return buildPolicyDenied(actionId, denialReason, traceId, baseUrl);
    }

    public List<Map<String, String>> browserFallbackLinks(
            String baseUrl,
            String taskId,
            String goalId,
            String artifactId,
            String auditId
    ) {
        String normalizedBase = Objects.toString(baseUrl, "").trim().replaceAll("/+$", "");
        List<Map<String, String>> links = new ArrayList<>();
        if (!Objects.toString(taskId, "").isBlank()) {
            links.add(link("task", normalizedBase + "/tasks/" + taskId));
        }
        if (!Objects.toString(goalId, "").isBlank()) {
            links.add(link("goal", normalizedBase + "/goals/" + goalId));
        }
        if (!Objects.toString(artifactId, "").isBlank()) {
            links.add(link("artifact", normalizedBase + "/artifacts/" + artifactId));
        }
        if (!Objects.toString(auditId, "").isBlank()) {
            links.add(link("audit_trace", normalizedBase + "/api/system/audit-logs?object=" + auditId));
        }
        if (links.isEmpty()) {
            links.add(link("governance", normalizedBase + "/governance"));
            links.add(link("audit", normalizedBase + "/api/system/audit-logs"));
        }
        return links;
    }

    private static String extractTraceHint(ClientResponse response) {
        if (response == null || response.getResponseBody() == null) {
            return "";
        }
        String body = response.getResponseBody();
        int marker = body.indexOf("trace");
        if (marker < 0) {
            return "";
        }
        int end = Math.min(body.length(), marker + 80);
        return body.substring(marker, end).replaceAll("[\\r\\n\\t]", " ").trim();
    }

    private static Map<String, String> link(String name, String url) {
        Map<String, String> value = new LinkedHashMap<>();
        value.put("name", name);
        value.put("url", url);
        return value;
    }

    public record PolicyDeniedModel(
            String actionId,
            String denialReason,
            String traceId,
            List<Map<String, String>> browserFallbackLinks,
            List<String> nextSteps,
            boolean fallbackRequiresBackendAuthorization
    ) {
        public PolicyDeniedModel {
            browserFallbackLinks = browserFallbackLinks == null ? List.of() : List.copyOf(browserFallbackLinks);
            nextSteps = nextSteps == null ? List.of() : List.copyOf(nextSteps);
        }
    }
}
