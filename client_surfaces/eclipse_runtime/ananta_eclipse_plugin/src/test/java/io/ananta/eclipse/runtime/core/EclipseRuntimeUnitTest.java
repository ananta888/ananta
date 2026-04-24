package io.ananta.eclipse.runtime.core;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.commands.RuntimeCommandExecutionResult;
import io.ananta.eclipse.runtime.commands.RuntimeCommandType;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.testsupport.StubBackendServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EclipseRuntimeUnitTest {
    @Test
    void profileValidationAndPersistenceRemainSafe() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new ClientProfile("dev", "localhost:8080", "session_token", "local", "token", 15)
        );

        ClientProfile profile = new ClientProfile(
                "dev",
                "http://localhost:8080/",
                "session_token",
                "local",
                "secret-token",
                120
        );
        assertEquals(60, profile.getTimeoutSeconds());
        assertTrue(profile.hasAuthToken());
        assertFalse(profile.toPersistenceMap().containsKey("auth_token"));
    }

    @Test
    void capabilityGateFailsClosedForUnauthorizedActions() {
        CapabilityGate gate = new CapabilityGate(
                Set.of("goals", "tasks"),
                Map.of(
                        "io.ananta.eclipse.command.analyze", true,
                        "io.ananta.eclipse.command.patch", false
                )
        );

        CapabilityGate.GateDecision analyzeDecision = gate.evaluate("io.ananta.eclipse.command.analyze", "goals");
        CapabilityGate.GateDecision patchDenied = gate.evaluate("io.ananta.eclipse.command.patch", "patch");
        CapabilityGate.GateDecision unknownDenied = gate.evaluate("io.ananta.eclipse.command.unknown", "tasks");

        assertTrue(analyzeDecision.allowed());
        assertFalse(patchDenied.allowed());
        assertEquals("permission_denied", patchDenied.reason());
        assertFalse(unknownDenied.allowed());
    }

    @Test
    void degradedStateMappingAndRetryPolicyStayDeterministic() {
        assertEquals(DegradedState.HEALTHY, AnantaApiClient.mapStatusToState(200, false));
        assertEquals(DegradedState.AUTH_FAILED, AnantaApiClient.mapStatusToState(401, false));
        assertEquals(DegradedState.POLICY_DENIED, AnantaApiClient.mapStatusToState(403, false));
        assertEquals(DegradedState.CAPABILITY_MISSING, AnantaApiClient.mapStatusToState(422, false));
        assertEquals(DegradedState.BACKEND_UNREACHABLE, AnantaApiClient.mapStatusToState(503, false));
        assertEquals(DegradedState.MALFORMED_RESPONSE, AnantaApiClient.mapStatusToState(200, true));
        assertTrue(AnantaApiClient.isRetriable(DegradedState.MALFORMED_RESPONSE));
        assertFalse(AnantaApiClient.isRetriable(DegradedState.POLICY_DENIED));
    }

    @Test
    void contextCaptureRemainsBoundedAndRejectsUnrelatedPaths() {
        EclipseContextCaptureRuntime captureRuntime = new EclipseContextCaptureRuntime(50, 40, 2);
        EclipseContextCaptureRuntime.BoundedContextPayload payload = captureRuntime.capture(
                new EclipseContextCaptureRuntime.WorkspaceState(
                        "/workspace",
                        "demo",
                        "/workspace/src/main.py",
                        List.of("/workspace/src/a.py", "/outside/leak.py", "/workspace/src/b.py", "/workspace/src/c.py")
                ),
                new EclipseContextCaptureRuntime.EditorState(
                        "/workspace/src/main.py",
                        "X".repeat(80),
                        "Y".repeat(80)
                )
        );

        assertTrue(payload.bounded());
        assertTrue(payload.userReviewRequiredBeforeSend());
        assertEquals(2, payload.selectedPaths().size());
        assertEquals(List.of("/outside/leak.py"), payload.rejectedPaths());
        assertTrue(payload.selectionClipped());
        assertTrue(payload.fileContentClipped());
        assertTrue(payload.toContextJson().contains("\"implicit_unrelated_paths_included\":false"));
    }

    @Test
    void commandRegistryDispatchesAnalyzeWithCapabilityGate() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/tasks/analyze", 200, "{\"task_id\":\"task-analyze-1\",\"status\":\"queued\"}");
            ClientProfile profile = new ClientProfile("dev", backend.baseUrl(), "session_token", "local", "token", 15);
            AnantaApiClient apiClient = new AnantaApiClient(profile);
            CapabilityGate gate = new CapabilityGate(
                    Set.of("goals"),
                    Map.of(RuntimeCommandType.ANALYZE.commandId(), true)
            );
            EclipseCommandRegistry registry = new EclipseCommandRegistry(
                    apiClient,
                    gate,
                    new EclipseContextCaptureRuntime()
            );
            RuntimeCommandExecutionResult result = registry.execute(
                    new EclipseCommandRegistry.CommandInvocation(
                            RuntimeCommandType.ANALYZE.commandId(),
                            "Analyze module",
                            "repository_understanding",
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

            assertTrue(result.isAllowed());
            assertEquals(DegradedState.HEALTHY, result.getResponse().getState());
            assertFalse(backend.findRequests("POST", "/tasks/analyze").isEmpty());
        }
    }
}
