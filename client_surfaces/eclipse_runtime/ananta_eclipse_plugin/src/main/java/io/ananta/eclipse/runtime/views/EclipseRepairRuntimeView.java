package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.Objects;

public final class EclipseRepairRuntimeView {
    private final AnantaApiClient apiClient;
    private final CapabilityGate capabilityGate;

    public EclipseRepairRuntimeView(AnantaApiClient apiClient, CapabilityGate capabilityGate) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
        this.capabilityGate = Objects.requireNonNull(capabilityGate, "capabilityGate");
    }

    public RepairExplorerModel loadRepairExplorer(String repairSessionId) {
        ClientResponse sessions = apiClient.listRepairs();
        ClientResponse detail = null;
        String normalizedSessionId = Objects.toString(repairSessionId, "").trim();
        if (!normalizedSessionId.isBlank()) {
            detail = apiClient.getRepairSession(normalizedSessionId);
        }
        return new RepairExplorerModel(sessions, detail, true, true);
    }

    public RepairStepActionResult approveRepairStep(
            String repairSessionId,
            String stepId,
            boolean userConfirmed,
            String comment
    ) {
        if (!userConfirmed) {
            return RepairStepActionResult.denied("explicit_confirmation_required");
        }
        CapabilityGate.GateDecision gateDecision = capabilityGate.evaluate(
                "repair:approve_step",
                "repair_step_approval"
        );
        if (!gateDecision.allowed()) {
            return RepairStepActionResult.denied(gateDecision.reason());
        }
        ClientResponse response = apiClient.approveRepairStep(repairSessionId, stepId, comment);
        return RepairStepActionResult.executed(response);
    }

    public record RepairExplorerModel(
            ClientResponse sessionsResponse,
            ClientResponse detailResponse,
            boolean readOnlyByDefault,
            boolean noExecutionOnOpenOrRefresh
    ) {
    }

    public record RepairStepActionResult(
            boolean actionAllowed,
            String denialReason,
            ClientResponse response
    ) {
        public static RepairStepActionResult denied(String denialReason) {
            return new RepairStepActionResult(false, denialReason, null);
        }

        public static RepairStepActionResult executed(ClientResponse response) {
            return new RepairStepActionResult(true, null, response);
        }
    }
}
