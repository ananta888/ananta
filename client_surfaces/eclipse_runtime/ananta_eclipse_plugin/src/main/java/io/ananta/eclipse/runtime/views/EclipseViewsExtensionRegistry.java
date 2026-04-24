package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;

import java.util.List;
import java.util.Map;
import java.util.Objects;

public final class EclipseViewsExtensionRegistry {
    private static final List<String> CORE_VIEW_IDS = List.of(
            "io.ananta.eclipse.view.goal",
            "io.ananta.eclipse.view.task_list",
            "io.ananta.eclipse.view.task_detail",
            "io.ananta.eclipse.view.artifact",
            "io.ananta.eclipse.view.approval_queue",
            "io.ananta.eclipse.view.audit",
            "io.ananta.eclipse.view.repair",
            "io.ananta.eclipse.view.tui_status",
            "io.ananta.eclipse.view.policy_fallback"
    );

    private final EclipseTaskRuntimeView taskRuntimeView;
    private final EclipseArtifactRuntimeView artifactRuntimeView;
    private final EclipseApprovalRuntimeView approvalRuntimeView;
    private final EclipseAuditRuntimeView auditRuntimeView;
    private final EclipseRepairRuntimeView repairRuntimeView;
    private final EclipseTuiRuntimeBridge tuiRuntimeBridge;
    private final EclipsePolicyFallbackUx policyFallbackUx;

    public EclipseViewsExtensionRegistry(AnantaApiClient apiClient, CapabilityGate capabilityGate) {
        Objects.requireNonNull(apiClient, "apiClient");
        Objects.requireNonNull(capabilityGate, "capabilityGate");
        this.taskRuntimeView = new EclipseTaskRuntimeView(apiClient);
        this.artifactRuntimeView = new EclipseArtifactRuntimeView(apiClient);
        this.approvalRuntimeView = new EclipseApprovalRuntimeView(apiClient, capabilityGate);
        this.auditRuntimeView = new EclipseAuditRuntimeView(apiClient);
        this.repairRuntimeView = new EclipseRepairRuntimeView(apiClient, capabilityGate);
        this.tuiRuntimeBridge = new EclipseTuiRuntimeBridge();
        this.policyFallbackUx = new EclipsePolicyFallbackUx();
    }

    public List<String> listRuntimeViewIds() {
        return CORE_VIEW_IDS;
    }

    public Map<String, Object> buildViewRegistrationSnapshot() {
        return Map.of(
                "schema", "eclipse_runtime_views_extension_registry_v1",
                "view_ids", CORE_VIEW_IDS,
                "task_runtime_view", taskRuntimeView.getClass().getSimpleName(),
                "artifact_runtime_view", artifactRuntimeView.getClass().getSimpleName(),
                "approval_runtime_view", approvalRuntimeView.getClass().getSimpleName(),
                "audit_runtime_view", auditRuntimeView.getClass().getSimpleName(),
                "repair_runtime_view", repairRuntimeView.getClass().getSimpleName(),
                "tui_runtime_bridge", tuiRuntimeBridge.getClass().getSimpleName(),
                "policy_fallback_ux", policyFallbackUx.getClass().getSimpleName()
        );
    }

    public EclipseTaskRuntimeView taskRuntimeView() {
        return taskRuntimeView;
    }

    public EclipseArtifactRuntimeView artifactRuntimeView() {
        return artifactRuntimeView;
    }

    public EclipseApprovalRuntimeView approvalRuntimeView() {
        return approvalRuntimeView;
    }

    public EclipseAuditRuntimeView auditRuntimeView() {
        return auditRuntimeView;
    }

    public EclipseRepairRuntimeView repairRuntimeView() {
        return repairRuntimeView;
    }

    public EclipseTuiRuntimeBridge tuiRuntimeBridge() {
        return tuiRuntimeBridge;
    }

    public EclipsePolicyFallbackUx policyFallbackUx() {
        return policyFallbackUx;
    }
}
