package io.ananta.eclipse.runtime.snake;

import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.testsupport.StubBackendServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaSnakeHubIntegrationRuntimeTest {
    @Test
    void registerAndHeartbeatUseHubEndpointsAndFallbackSafe() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/v1/snake/register", 200, "{\"client_id\":\"snake-1\"}");
            backend.stub("POST", "/v1/snake/heartbeat", 200, "{\"ok\":true}");

            AnantaSnakePluginService service = new AnantaSnakePluginService();
            try {
                ClientProfile profile = new ClientProfile("snake", backend.baseUrl(), "session_token", "local", "token", 10);
                service.applyHubProfile(profile, true);

                ClientResponse registered = service.registerSnakeClient("ws-demo", "Ananta Snake");
                assertTrue(registered.isOk());
                assertEquals("hub_connected", service.snapshot().getHubConnectionState());

                ClientResponse heartbeat = service.heartbeatNowForTest();
                assertTrue(heartbeat.isOk());
                assertEquals(1, backend.findRequests("POST", "/v1/snake/register").size());
                assertEquals(1, backend.findRequests("POST", "/v1/snake/heartbeat").size());
            } finally {
                service.shutdown();
            }
        }
    }

    @Test
    void contextDispatchIsDebouncedAndSendsMetadataOnlyEnvelope() throws Exception {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/goals", 200, "{\"goal_id\":\"snake-context-1\",\"status\":\"queued\"}");

            AnantaSnakePluginService service = new AnantaSnakePluginService();
            try {
                ClientProfile profile = new ClientProfile("snake", backend.baseUrl(), "session_token", "local", "token", 10);
                service.applyHubProfile(profile, true);
                service.registerSnakeClient("ws-demo", "Ananta Snake");
                service.recordActiveWorkbenchPart("org.eclipse.ui.editors", "Main.java");
                service.captureEditorContextSnapshot("demo", "/workspace/Main.java", "java_editor", 5, 11);

                service.queueContextEnvelopeDispatch("policy-1", List.of(), List.of("artifact://a?token=secret-123"));
                service.queueContextEnvelopeDispatch("policy-1", List.of(), List.of("artifact://a?token=secret-123"));
                service.queueContextEnvelopeDispatch("policy-1", List.of(), List.of("artifact://a?token=secret-123"));
                Thread.sleep(700L);

                var requests = backend.findRequests("POST", "/goals");
                assertEquals(1, requests.size());
                String body = requests.get(0).body();
                assertTrue(body.contains("\"schema\":\"eclipse_snake_context_envelope_v1\""));
                assertTrue(body.contains("\"policy_decision_ref\":\"policy-1\""));
                assertTrue(body.contains("\"includes_file_content\":false"));
                assertTrue(body.contains("token=***"));
                assertFalse(body.contains("secret-123"));
                assertTrue(service.lastPolicyReasonCode().contains("sensitive_values_redacted"));
            } finally {
                service.shutdown();
            }
        }
    }

    @Test
    void askActionUsesCurrentContextEnvelopeAndReturnsResponse() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/goals", 200, "{\"goal_id\":\"snake-ask-1\",\"status\":\"queued\"}");
            AnantaSnakePluginService service = new AnantaSnakePluginService();
            try {
                ClientProfile profile = new ClientProfile("snake", backend.baseUrl(), "session_token", "local", "token", 10);
                service.applyHubProfile(profile, true);
                service.registerSnakeClient("ws-demo", "Ananta Snake");
                service.recordActiveWorkbenchPart("org.eclipse.ui.views.ProblemView", "Problems");
                service.captureEditorContextSnapshot("demo", "/workspace/Main.java", "java_editor", 0, 0);

                ClientResponse ask = service.askAnantaSnakeNow("Ask Snake");
                assertTrue(ask.isOk());
                assertEquals("ai_active", service.snapshot().getHubConnectionState());
                assertTrue(service.lastAskResult().contains("ask_state=healthy"));
                assertEquals(1, backend.findRequests("POST", "/goals").size());
            } finally {
                service.shutdown();
            }
        }
    }

    @Test
    void externalProviderRemainsDeniedByDefault() throws IOException {
        try (StubBackendServer backend = StubBackendServer.start()) {
            backend.stub("POST", "/goals", 200, "{\"goal_id\":\"snake-ask-1\",\"status\":\"queued\"}");
            AnantaSnakePluginService service = new AnantaSnakePluginService();
            try {
                ClientProfile profile = new ClientProfile("snake", "https://example.com", "session_token", "local", "token", 10);
                service.applyHubProfile(profile, true);
                ClientResponse ask = service.askAnantaSnakeNow("Ask Snake");
                assertFalse(ask.isOk());
                assertEquals("local_only", service.snapshot().getHubConnectionState());
                assertEquals("external_provider_denied", service.lastPolicyReasonCode());
            } finally {
                service.shutdown();
            }
        }
    }
}
