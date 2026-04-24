package io.ananta.eclipse.runtime.integration;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.commands.RuntimeCommandExecutionResult;
import io.ananta.eclipse.runtime.commands.RuntimeCommandType;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.DegradedState;
import io.ananta.eclipse.runtime.testsupport.StubBackendServer;
import io.ananta.eclipse.runtime.views.EclipseArtifactRuntimeView;
import io.ananta.eclipse.runtime.views.EclipseApprovalRuntimeView;
import io.ananta.eclipse.runtime.views.EclipseRepairRuntimeView;
import io.ananta.eclipse.runtime.views.EclipseViewsExtensionRegistry;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EclipseRuntimeIntegrationUiTest {
    @Test
    void commandExecutionAndMainViewsLoadFromRuntime() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/tasks/review", 200, "{\"task_id\":\"task-review-1\",\"status\":\"queued\"}");
            backend.stub("GET", "/tasks", 200, "{\"items\":[{\"id\":\"task-1\",\"status\":\"in_progress\"}]}");
            backend.stub("GET", "/tasks/task-1", 200, "{\"id\":\"task-1\",\"status\":\"in_progress\"}");
            backend.stub("GET", "/artifacts", 200, "{\"items\":[{\"id\":\"artifact-1\",\"type\":\"diff\"}]}");
            backend.stub("GET", "/artifacts/artifact-1", 200, "{\"id\":\"artifact-1\",\"type\":\"diff\"}");
            backend.stub("GET", "/approvals", 200, "{\"items\":[{\"id\":\"approval-1\",\"state\":\"pending\"}]}");
            backend.stub("GET", "/audit?severity=high&type=policy&object=task-1", 200, "{\"items\":[{\"id\":\"audit-1\"}]}");
            backend.stub("GET", "/repairs", 200, "{\"items\":[{\"id\":\"repair-1\"}]}");
            backend.stub("GET", "/repairs/repair-1", 200, "{\"id\":\"repair-1\",\"state\":\"open\"}");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            CapabilityGate gate = new CapabilityGate(
                    Set.of("review", "approvals", "repair_step_approval"),
                    Map.of(
                            RuntimeCommandType.REVIEW.commandId(), true,
                            "approval:approve", true,
                            "approval:reject", true,
                            "repair:approve_step", true
                    )
            );
            EclipseCommandRegistry registry = new EclipseCommandRegistry(apiClient, gate, new EclipseContextCaptureRuntime());
            EclipseViewsExtensionRegistry viewsRegistry = new EclipseViewsExtensionRegistry(apiClient, gate);

            RuntimeCommandExecutionResult reviewResult = registry.execute(
                    new EclipseCommandRegistry.CommandInvocation(
                            RuntimeCommandType.REVIEW.commandId(),
                            "Review selection",
                            "change_review",
                            profile.getProfileId(),
                            null,
                            null,
                            new EclipseContextCaptureRuntime.WorkspaceState(
                                    "/workspace",
                                    "demo",
                                    "/workspace/src/main.py",
                                    List.of("/workspace/src/main.py")
                            ),
                            new EclipseContextCaptureRuntime.EditorState(
                                    "/workspace/src/main.py",
                                    "def f():\n  return 1",
                                    "def f():\n  return 1"
                            )
                    )
            );
            assertTrue(reviewResult.isAllowed());
            assertEquals(DegradedState.HEALTHY, reviewResult.getResponse().getState());

            var taskView = viewsRegistry.taskRuntimeView().loadTaskViews("task-1");
            assertTrue(taskView.refreshControlsVisible());
            assertFalse(taskView.staleOrMissingState());
            assertEquals(DegradedState.HEALTHY, taskView.taskListResponse().getState());
            assertEquals(DegradedState.HEALTHY, taskView.taskDetailResponse().getState());

            var artifactView = viewsRegistry.artifactRuntimeView().loadArtifactViews("artifact-1");
            assertEquals(DegradedState.HEALTHY, artifactView.artifactListResponse().getState());
            assertEquals(DegradedState.HEALTHY, artifactView.artifactDetailResponse().getState());

            var auditView = viewsRegistry.auditRuntimeView().loadAuditExplorer(
                    new io.ananta.eclipse.runtime.views.EclipseAuditRuntimeView.AuditFilters("high", "policy", "task-1")
            );
            assertEquals(DegradedState.HEALTHY, auditView.response().getState());

            assertTrue(viewsRegistry.listRuntimeViewIds().contains("io.ananta.eclipse.view.task_detail"));
            assertFalse(backend.findRequests("POST", "/tasks/review").isEmpty());
        }
    }

    @Test
    void contextPreviewAndExplicitConfirmationPathsAreEnforced() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/tasks/patch-plan", 200, "{\"task_id\":\"task-patch-1\",\"status\":\"queued\"}");
            backend.stub("GET", "/approvals", 200, "{\"items\":[{\"id\":\"approval-1\"}]}");
            backend.stub("GET", "/repairs", 200, "{\"items\":[{\"id\":\"repair-1\"}]}");
            backend.stub("GET", "/repairs/repair-1", 200, "{\"id\":\"repair-1\"}");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            CapabilityGate gate = new CapabilityGate(
                    Set.of("patch", "approvals", "repair_step_approval"),
                    Map.of(
                            RuntimeCommandType.PATCH.commandId(), true,
                            "approval:approve", true,
                            "approval:reject", true,
                            "repair:approve_step", true
                    )
            );
            EclipseCommandRegistry registry = new EclipseCommandRegistry(
                    apiClient,
                    gate,
                    new EclipseContextCaptureRuntime(80, 80, 3)
            );
            RuntimeCommandExecutionResult patchResult = registry.execute(
                    new EclipseCommandRegistry.CommandInvocation(
                            RuntimeCommandType.PATCH.commandId(),
                            "Patch",
                            "bugfix_planning",
                            profile.getProfileId(),
                            null,
                            null,
                            new EclipseContextCaptureRuntime.WorkspaceState(
                                    "/workspace",
                                    "demo",
                                    "/workspace/src/main.py",
                                    List.of("/workspace/src/main.py", "/outside/leak.py")
                            ),
                            new EclipseContextCaptureRuntime.EditorState(
                                    "/workspace/src/main.py",
                                    "X".repeat(200),
                                    "Y".repeat(200)
                            )
                    )
            );
            assertTrue((Boolean) patchResult.getContextPreview().get("selection_clipped"));
            assertEquals(List.of("/outside/leak.py"), patchResult.getContextPreview().get("rejected_paths"));

            EclipseApprovalRuntimeView approvalView = new EclipseApprovalRuntimeView(apiClient, gate);
            var deniedApproval = approvalView.runApprovalAction("approval-1", "approve", false, "no confirm");
            assertFalse(deniedApproval.actionAllowed());
            assertEquals("explicit_confirmation_required", deniedApproval.denialReason());

            EclipseRepairRuntimeView repairView = new EclipseRepairRuntimeView(apiClient, gate);
            var deniedRepair = repairView.approveRepairStep("repair-1", "step-1", false, "no confirm");
            assertFalse(deniedRepair.actionAllowed());
            assertEquals("explicit_confirmation_required", deniedRepair.denialReason());
        }
    }

    @Test
    void uiModelsNeverAutoApplyOrImplicitlyExecuteRepairSteps() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("GET", "/repairs", 200, "{\"items\":[{\"id\":\"repair-1\"}]}");
            backend.stub("GET", "/repairs/repair-1", 200, "{\"id\":\"repair-1\",\"state\":\"open\"}");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            CapabilityGate gate = new CapabilityGate(Set.of("repair_step_approval"), Map.of("repair:approve_step", true));

            EclipseArtifactRuntimeView artifactView = new EclipseArtifactRuntimeView(apiClient);
            var diffRender = artifactView.renderDiffReferences(
                    "diff",
                    List.of(new EclipseArtifactRuntimeView.DiffHunkReference("src/main.py", 14))
            );
            assertEquals("proposal_review", diffRender.renderMode());
            assertTrue(diffRender.neverAutoApplyVisibleChanges());

            EclipseRepairRuntimeView repairView = new EclipseRepairRuntimeView(apiClient, gate);
            var explorer = repairView.loadRepairExplorer("repair-1");
            assertTrue(explorer.readOnlyByDefault());
            assertTrue(explorer.noExecutionOnOpenOrRefresh());
            assertEquals(DegradedState.HEALTHY, explorer.sessionsResponse().getState());
            assertEquals(DegradedState.HEALTHY, explorer.detailResponse().getState());
            assertTrue(backend.findRequests("POST", "/repairs/repair-1/steps/step-1/approve").isEmpty());
        }
    }
}
