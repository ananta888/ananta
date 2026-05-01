package io.ananta.eclipse.runtime.product;

import io.ananta.eclipse.runtime.chat.ChatContextSelector;
import io.ananta.eclipse.runtime.chat.ChatHistoryStore;
import io.ananta.eclipse.runtime.chat.ChatRuntimeModel;
import io.ananta.eclipse.runtime.completion.AnantaCompletionProposalComputer;
import io.ananta.eclipse.runtime.completion.AnantaQuickFixProcessor;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.DegradedState;
import io.ananta.eclipse.runtime.patch.ApprovalGatedPatchApplier;
import io.ananta.eclipse.runtime.patch.PatchPreviewViewModel;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeServices;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.platform.RuntimeUiState;
import io.ananta.eclipse.runtime.preferences.AnantaPreferencePage;
import io.ananta.eclipse.runtime.project.BlueprintSelectionPage;
import io.ananta.eclipse.runtime.project.EvolutionProposalViewModel;
import io.ananta.eclipse.runtime.project.EvolveProjectWizard;
import io.ananta.eclipse.runtime.project.GeneratedProjectImporter;
import io.ananta.eclipse.runtime.project.NewAnantaProjectWizard;
import io.ananta.eclipse.runtime.repair.ProblemMarkerContextCapture;
import io.ananta.eclipse.runtime.review.ReviewFeedbackRuntime;
import io.ananta.eclipse.runtime.security.ActionPolicyRuntime;
import io.ananta.eclipse.runtime.security.WorkspaceTrustGuard;
import io.ananta.eclipse.runtime.testsupport.StubBackendServer;
import io.ananta.eclipse.runtime.views.DegradedViewModel;
import io.ananta.eclipse.runtime.workspace.WorkspaceWriteGuard;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EclipseProductRuntimeModelTest {
    @Test
    void runtimeSessionPreferencesAndDegradedStatesAreCentralized() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("GET", "/health", 200, "{\"status\":\"ok\"}");
            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "secret", 15);
            AnantaRuntimeServices services = new AnantaRuntimeServices(
                    new AnantaApiClient(profile),
                    new CapabilityGate(Set.of("goals"), Map.of("io.ananta.eclipse.command.analyze", true)),
                    new EclipseContextCaptureRuntime()
            );
            AnantaRuntimeSession session = new AnantaRuntimeSession(services);

            RuntimeUiState state = session.refreshHealth();
            assertEquals(DegradedState.HEALTHY, state.getState());
            assertTrue(state.isActionsEnabled());

            var validation = new AnantaPreferencePage().validate(
                    new AnantaPreferencePage.ProfilePreferenceDraft("dev", backend.baseUrl(), "session_token", "local", "token", 15)
            );
            assertTrue(validation.valid());
            assertFalse(validation.persistedProfile().containsKey("auth_token"));

            var degraded = new DegradedViewModel().from(DegradedState.BACKEND_UNREACHABLE, "offline");
            assertTrue(degraded.retryVisible());
            assertFalse(degraded.contentEnabled());
        }
    }

    @Test
    void chatContextAndHistoryRemainBoundedAndRedacted() {
        EclipseContextCaptureRuntime capture = new EclipseContextCaptureRuntime(20, 20, 2);
        var payload = capture.capture(
                new EclipseContextCaptureRuntime.WorkspaceState("/workspace", "demo", "/workspace/A.java", List.of("/workspace/A.java")),
                new EclipseContextCaptureRuntime.EditorState("/workspace/A.java", "X".repeat(50), "Y".repeat(50))
        );

        var selected = new ChatContextSelector().select(true, false, payload);
        assertTrue(selected.bounded());
        assertTrue(selected.userReviewRequired());
        assertEquals(20, selected.selectionText().length());

        ChatRuntimeModel model = new ChatRuntimeModel();
        ChatHistoryStore store = new ChatHistoryStore(true);
        store.append(model.addMessage("user", "token=abc please review", List.of("task:T-1")));
        assertTrue(store.messages().get(0).text().contains("token=***"));
        assertTrue(store.isSessionOnly());
    }

    @Test
    void patchApplyProjectRepairCompletionAndPolicyModelsAreApprovalSafe() {
        WorkspaceWriteGuard writeGuard = new WorkspaceWriteGuard();
        ApprovalGatedPatchApplier applier = new ApprovalGatedPatchApplier(writeGuard);
        assertFalse(applier.canApply(false, true, "/workspace", "/workspace/A.java").allowed());
        assertFalse(applier.canApply(true, false, "/workspace", "/workspace/A.java").allowed());
        assertTrue(applier.canApply(true, true, "/workspace", "/workspace/A.java").allowed());
        assertFalse(applier.canApply(true, true, "/workspace", "/outside/A.java").allowed());

        PatchPreviewViewModel preview = new PatchPreviewViewModel(List.of(
                new PatchPreviewViewModel.PatchHunk("/workspace/A.java", 4, "+ code", true)
        ));
        assertTrue(preview.neverAutoApply());
        assertTrue(preview.hasSelectedHunks());

        var markers = new ProblemMarkerContextCapture().capture(List.of(
                new ProblemMarkerContextCapture.ProblemMarker("demo", "/workspace/A.java", 3, "ERROR", "broken")
        ), 5);
        assertTrue(markers.bounded());

        var projectRequest = new NewAnantaProjectWizard().buildRequest("/workspace/new", "java", "clean architecture", List.of("tests"), "default");
        assertTrue(projectRequest.createsHubGoalFirst());
        assertTrue(new BlueprintSelectionPage().select("bp-1", "wp-1", List.of("bp-1")).available());
        assertTrue(new GeneratedProjectImporter(writeGuard).preview("/workspace", List.of("/workspace/new/pom.xml")).importAllowed());
        assertTrue(new EvolveProjectWizard().buildRequest("demo", "add tests", List.of("/workspace/A.java")).hubGoalFirst());
        assertTrue(new EvolutionProposalViewModel().build("P-1", List.of("A.java"), List.of("low"), List.of("mvn test")).approvalGated());

        var completion = new AnantaCompletionProposalComputer().buildRequest("pri", "{}", false);
        assertTrue(completion.policyLimited());
        assertFalse(new AnantaCompletionProposalComputer().proposals(List.of("println")).get(0).autoApply());
        assertTrue(new AnantaQuickFixProcessor().build("m-1", "Fix", List.of("/workspace/A.java"), true).previewRequired());

        var review = new ReviewFeedbackRuntime().build("T-1", List.of(new ReviewFeedbackRuntime.HunkComment("A.java", 3, "ok")));
        assertTrue(review.sentToHubReviewFlow());

        var policy = new ActionPolicyRuntime(
                new CapabilityGate(Set.of("goals"), Map.of("chat", true)),
                Map.of("chat", "goals")
        );
        assertTrue(policy.evaluate("chat").allowed());
        assertFalse(policy.evaluate("unknown").allowed());

        assertFalse(new WorkspaceTrustGuard(Set.of("/workspace")).evaluate("/other", true).allowed());
    }
}
