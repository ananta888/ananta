package io.ananta.eclipse.runtime.commands;

import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.LinkedHashMap;
import java.util.Map;

public final class RuntimeCommandExecutionResult {
    private final String commandId;
    private final boolean allowed;
    private final String denialReason;
    private final ClientResponse response;
    private final Map<String, Object> contextPreview;
    private final String browserFallbackUrl;

    private RuntimeCommandExecutionResult(
            String commandId,
            boolean allowed,
            String denialReason,
            ClientResponse response,
            Map<String, Object> contextPreview,
            String browserFallbackUrl
    ) {
        this.commandId = commandId;
        this.allowed = allowed;
        this.denialReason = denialReason;
        this.response = response;
        this.contextPreview = contextPreview == null ? Map.of() : new LinkedHashMap<>(contextPreview);
        this.browserFallbackUrl = browserFallbackUrl;
    }

    public static RuntimeCommandExecutionResult denied(
            String commandId,
            String denialReason,
            Map<String, Object> contextPreview,
            String browserFallbackUrl
    ) {
        return new RuntimeCommandExecutionResult(commandId, false, denialReason, null, contextPreview, browserFallbackUrl);
    }

    public static RuntimeCommandExecutionResult executed(
            String commandId,
            ClientResponse response,
            Map<String, Object> contextPreview,
            String browserFallbackUrl
    ) {
        return new RuntimeCommandExecutionResult(commandId, true, null, response, contextPreview, browserFallbackUrl);
    }

    public String getCommandId() {
        return commandId;
    }

    public boolean isAllowed() {
        return allowed;
    }

    public String getDenialReason() {
        return denialReason;
    }

    public ClientResponse getResponse() {
        return response;
    }

    public Map<String, Object> getContextPreview() {
        return contextPreview;
    }

    public String getBrowserFallbackUrl() {
        return browserFallbackUrl;
    }

    public boolean isPolicyDenied() {
        return !allowed || (response != null && response.getState().name().equals("POLICY_DENIED"));
    }

    public Map<String, Object> toPreviewMap() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("schema", "eclipse_runtime_command_execution_result_v1");
        payload.put("command_id", commandId);
        payload.put("allowed", allowed);
        payload.put("denial_reason", denialReason);
        payload.put("browser_fallback_url", browserFallbackUrl);
        payload.put("context_preview", contextPreview);
        if (response != null) {
            payload.put("response_state", response.getState().name().toLowerCase());
            payload.put("response_status_code", response.getStatusCode());
            payload.put("retriable", response.isRetriable());
        }
        return payload;
    }
}
