package io.ananta.eclipse.runtime.security;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.commands.RuntimeCommandExecutionResult;
import io.ananta.eclipse.runtime.commands.RuntimeCommandType;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.DegradedState;
import io.ananta.eclipse.runtime.testsupport.StubBackendServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EclipseRuntimeSecurityGovernanceTest {
    @Test
    void runtimeActionsCannotBypassCapabilityOrPolicyGates() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/goals", 200, "{\"goal_id\":\"goal-review-1\"}");
            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            CapabilityGate gate = new CapabilityGate(Set.of("review"), Map.of(RuntimeCommandType.REVIEW.commandId(), false));
            EclipseCommandRegistry registry = new EclipseCommandRegistry(apiClient, gate, new EclipseContextCaptureRuntime());

            RuntimeCommandExecutionResult denied = registry.execute(
                    new EclipseCommandRegistry.CommandInvocation(
                            RuntimeCommandType.REVIEW.commandId(),
                            "Review",
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
                                    "print('x')",
                                    "print('x')"
                            )
                    )
            );

            assertFalse(denied.isAllowed());
            assertEquals("permission_denied", denied.getDenialReason());
            assertTrue(backend.findRequests("POST", "/goals").isEmpty());
        }
    }

    @Test
    void tokenAndSecretRedactionStaysConsistent() {
        assertEquals(
                "token=*** password=*** api_key=***",
                TokenRedaction.redactSensitiveText("token=abc password=abc api_key=abc")
        );
        assertTrue(TokenRedaction.containsSensitiveKey("auth_token"));
        assertTrue(TokenRedaction.containsSensitiveKey("private_key_material"));
        assertFalse(TokenRedaction.containsSensitiveKey("project_id"));
    }

    @Test
    void deniedUnauthorizedStaleAndMalformedResponsesMapToExplicitStates() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/goals", 200, "not-json");

            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            String contextJson = "{\"schema\":\"client_bounded_context_payload_v1\",\"selection_text\":\"x\"}";

            var malformed = apiClient.reviewContext(contextJson);
            backend.stub("POST", "/goals", 403, "{\"error\":\"policy_denied\"}");
            var denied = apiClient.patchPlan(contextJson);
            backend.stub("POST", "/goals", 401, "{\"error\":\"unauthorized\"}");
            var unauthorized = apiClient.createProjectEvolve("Evolve", contextJson, "bp-1", "wp-1");
            backend.stub("POST", "/goals", 409, "{\"error\":\"stale_state\"}");
            var stale = apiClient.analyzeContext(contextJson);

            assertEquals(DegradedState.MALFORMED_RESPONSE, malformed.getState());
            assertTrue(malformed.isRetriable());
            assertEquals(DegradedState.POLICY_DENIED, denied.getState());
            assertFalse(denied.isRetriable());
            assertEquals(DegradedState.AUTH_FAILED, unauthorized.getState());
            assertEquals(DegradedState.UNKNOWN_ERROR, stale.getState());
            assertTrue(stale.isRetriable());
        }
    }
}
