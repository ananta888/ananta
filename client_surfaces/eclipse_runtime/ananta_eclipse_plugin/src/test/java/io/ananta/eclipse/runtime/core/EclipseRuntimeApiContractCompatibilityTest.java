package io.ananta.eclipse.runtime.core;

import io.ananta.eclipse.runtime.testsupport.StubBackendServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EclipseRuntimeApiContractCompatibilityTest {
    @Test
    void contractLocksGoalAndProjectRequestPayloadFields() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/goals", 200, "{\"goal_id\":\"goal-1\",\"task_id\":\"task-1\"}");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            String context = "{\"schema\":\"client_bounded_context_payload_v1\",\"selection_text\":\"print('x')\"}";

            var goal = apiClient.submitGoal(
                    "Analyze module",
                    context,
                    "repository_understanding",
                    "io.ananta.eclipse.command.analyze",
                    profile.getProfileId()
            );
            var projectNew = apiClient.createProjectNew("Create project", context, "bp-1", "wp-1");
            var projectEvolve = apiClient.createProjectEvolve("Evolve project", context, "bp-2", "wp-2");

            assertTrue(goal.isOk());
            assertTrue(projectNew.isOk());
            assertTrue(projectEvolve.isOk());

            var goalRequest = backend.findRequests("POST", "/goals").get(0);
            assertEquals(3, backend.findRequests("POST", "/goals").size());
            assertTrue(goalRequest.body().contains("\"goal\":\"Analyze module\""));
            assertTrue(goalRequest.body().contains("\"context\":{\"schema\":\"client_bounded_context_payload_v1\""));
            assertTrue(goalRequest.body().contains("\"operation_preset\":\"repository_understanding\""));
            assertTrue(goalRequest.body().contains("\"command_id\":\"io.ananta.eclipse.command.analyze\""));
            assertTrue(goalRequest.body().contains("\"profile_id\":\"dev\""));

            var projectNewRequest = backend.findRequests("POST", "/goals").get(1);
            assertTrue(projectNewRequest.body().contains("\"operation_preset\":\"new_project\""));
            assertTrue(projectNewRequest.body().contains("\"command_id\":\"io.ananta.eclipse.command.new_project\""));

            var projectEvolveRequest = backend.findRequests("POST", "/goals").get(2);
            assertTrue(projectEvolveRequest.body().contains("\"operation_preset\":\"project_evolution\""));
            assertTrue(projectEvolveRequest.body().contains("\"command_id\":\"io.ananta.eclipse.command.evolve_project\""));
        }
    }

    @Test
    void contractLocksRuntimeEndpointPathsAndAuditQueryShape() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("GET", "/tasks/task-1", 200, "{\"id\":\"task-1\"}");
            backend.stub("GET", "/tasks/repair-1", 200, "{\"id\":\"repair-1\"}");
            backend.stub("GET", "/artifacts/artifact-1", 200, "{\"id\":\"artifact-1\"}");
            backend.stub("GET", "/api/system/audit-logs?limit=100&severity=high&type=policy&object=task-1", 200, "{\"items\":[]}");
            backend.stub("POST", "/tasks/approval-1/review", 200, "{\"state\":\"reviewed\"}");
            backend.stub("POST", "/tasks/repair-1/review", 200, "{\"state\":\"approved\"}");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);

            assertTrue(apiClient.getTask("task-1").isOk());
            assertTrue(apiClient.getArtifact("artifact-1").isOk());
            assertTrue(apiClient.getRepairSession("repair-1").isOk());
            assertTrue(apiClient.listAuditEvents("high", "policy", "task-1").isOk());
            assertTrue(apiClient.approveApproval("approval-1", "ok").isOk());
            assertTrue(apiClient.rejectApproval("approval-1", "no").isOk());
            assertTrue(apiClient.approveRepairStep("repair-1", "step-1", "approved").isOk());

            var auditRequest = backend.findRequests("GET", "/api/system/audit-logs").get(0);
            assertEquals("limit=100&severity=high&type=policy&object=task-1", auditRequest.query());

            var approvalReviewRequests = backend.findRequests("POST", "/tasks/approval-1/review");
            assertEquals("{\"action\":\"approve\",\"comment\":\"ok\"}", approvalReviewRequests.get(0).body());
            assertEquals("{\"action\":\"reject\",\"comment\":\"no\"}", approvalReviewRequests.get(1).body());
            var repairApproveRequest = backend.findRequests("POST", "/tasks/repair-1/review").get(0);
            assertEquals("{\"action\":\"approve\",\"comment\":\"approved repair_step=step-1\"}", repairApproveRequest.body());
        }
    }

    @Test
    void degradedStateContractPayloadsRemainStable() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("GET", "/v1/ananta/capabilities", 422, "{\"error\":\"capability_missing\"}");
            backend.stub("POST", "/goals", 200, "not-json");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            String context = "{\"schema\":\"client_bounded_context_payload_v1\",\"selection_text\":\"x\"}";

            var capabilities = apiClient.getCapabilities();
            var malformedReview = apiClient.reviewContext(context);

            assertEquals(DegradedState.CAPABILITY_MISSING, capabilities.getState());
            assertEquals("request_failed:capability_missing", capabilities.getError());
            assertFalse(capabilities.isRetriable());

            assertEquals(DegradedState.MALFORMED_RESPONSE, malformedReview.getState());
            assertEquals("request_failed:malformed_response", malformedReview.getError());
            assertTrue(malformedReview.isRetriable());
        }
    }
}
