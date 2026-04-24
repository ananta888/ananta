package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.security.TokenRedaction;

import java.util.Map;
import java.util.Objects;

public final class EclipseAuditRuntimeView {
    private final AnantaApiClient apiClient;

    public EclipseAuditRuntimeView(AnantaApiClient apiClient) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
    }

    public AuditExplorerModel loadAuditExplorer(AuditFilters filters) {
        AuditFilters normalizedFilters = filters == null ? new AuditFilters(null, null, null) : filters;
        ClientResponse response = apiClient.listAuditEvents(
                normalizedFilters.severity(),
                normalizedFilters.eventType(),
                normalizedFilters.objectId()
        );
        String safePreview = TokenRedaction.redactSensitiveText(
                Objects.toString(response.getResponseBody(), "")
        );
        return new AuditExplorerModel(
                response,
                normalizedFilters,
                safePreview,
                true,
                true
        );
    }

    public record AuditFilters(
            String severity,
            String eventType,
            String objectId
    ) {
    }

    public record AuditExplorerModel(
            ClientResponse response,
            AuditFilters filters,
            String redactedPreview,
            boolean filterSupportEnabled,
            boolean sensitiveFieldsRedacted
    ) {
        public Map<String, Object> toPreviewMap() {
            return Map.of(
                    "schema", "eclipse_runtime_audit_explorer_preview_v1",
                    "filter_support_enabled", filterSupportEnabled,
                    "sensitive_fields_redacted", sensitiveFieldsRedacted,
                    "response_state", response.getState().name().toLowerCase()
            );
        }
    }
}
