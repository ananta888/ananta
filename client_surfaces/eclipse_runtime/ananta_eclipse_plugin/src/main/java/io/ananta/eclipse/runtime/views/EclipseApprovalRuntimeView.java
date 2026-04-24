package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.Objects;

public final class EclipseApprovalRuntimeView {
    private final AnantaApiClient apiClient;
    private final CapabilityGate capabilityGate;

    public EclipseApprovalRuntimeView(AnantaApiClient apiClient, CapabilityGate capabilityGate) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
        this.capabilityGate = Objects.requireNonNull(capabilityGate, "capabilityGate");
    }

    public ClientResponse loadPendingApprovals() {
        return apiClient.listApprovals();
    }

    public ApprovalActionResult runApprovalAction(
            String approvalId,
            String action,
            boolean userConfirmed,
            String comment
    ) {
        String normalizedAction = Objects.toString(action, "").trim().toLowerCase();
        if (!userConfirmed) {
            return ApprovalActionResult.denied("explicit_confirmation_required");
        }
        CapabilityGate.GateDecision gateDecision = capabilityGate.evaluate(
                "approval:" + normalizedAction,
                "approvals"
        );
        if (!gateDecision.allowed()) {
            return ApprovalActionResult.denied(gateDecision.reason());
        }
        if ("approve".equals(normalizedAction)) {
            ClientResponse response = apiClient.approveApproval(approvalId, comment);
            return ApprovalActionResult.executed(response, "approval_action:approve");
        }
        if ("reject".equals(normalizedAction)) {
            ClientResponse response = apiClient.rejectApproval(approvalId, comment);
            return ApprovalActionResult.executed(response, "approval_action:reject");
        }
        return ApprovalActionResult.denied("unsupported_action");
    }

    public record ApprovalActionResult(
            boolean actionAllowed,
            String denialReason,
            ClientResponse response,
            String auditTraceReference
    ) {
        public static ApprovalActionResult denied(String denialReason) {
            return new ApprovalActionResult(false, denialReason, null, null);
        }

        public static ApprovalActionResult executed(ClientResponse response, String auditTraceReference) {
            return new ApprovalActionResult(true, null, response, auditTraceReference);
        }
    }
}
