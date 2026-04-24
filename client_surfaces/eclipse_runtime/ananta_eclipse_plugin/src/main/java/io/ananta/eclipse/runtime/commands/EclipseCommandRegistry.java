package io.ananta.eclipse.runtime.commands;

import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

public final class EclipseCommandRegistry {
    private final AnantaApiClient apiClient;
    private final CapabilityGate capabilityGate;
    private final EclipseContextCaptureRuntime contextCaptureRuntime;
    private final GoalSubmissionRuntimePanel goalSubmissionRuntimePanel;
    private final Map<String, RuntimeCommandHandler> handlersByCommandId;

    public EclipseCommandRegistry(
            AnantaApiClient apiClient,
            CapabilityGate capabilityGate,
            EclipseContextCaptureRuntime contextCaptureRuntime
    ) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
        this.capabilityGate = Objects.requireNonNull(capabilityGate, "capabilityGate");
        this.contextCaptureRuntime = Objects.requireNonNull(contextCaptureRuntime, "contextCaptureRuntime");
        this.goalSubmissionRuntimePanel = new GoalSubmissionRuntimePanel(apiClient);
        this.handlersByCommandId = registerHandlers();
    }

    private static Map<String, RuntimeCommandHandler> registerHandlers() {
        Map<String, RuntimeCommandHandler> handlers = new LinkedHashMap<>();
        handlers.put(
                RuntimeCommandType.ANALYZE.commandId(),
                new AnalyzeReviewPatchRuntimeHandler(RuntimeCommandType.ANALYZE)
        );
        handlers.put(
                RuntimeCommandType.REVIEW.commandId(),
                new AnalyzeReviewPatchRuntimeHandler(RuntimeCommandType.REVIEW)
        );
        handlers.put(
                RuntimeCommandType.PATCH.commandId(),
                new AnalyzeReviewPatchRuntimeHandler(RuntimeCommandType.PATCH)
        );
        handlers.put(
                RuntimeCommandType.NEW_PROJECT.commandId(),
                new ProjectRuntimeHandler(RuntimeCommandType.NEW_PROJECT)
        );
        handlers.put(
                RuntimeCommandType.EVOLVE_PROJECT.commandId(),
                new ProjectRuntimeHandler(RuntimeCommandType.EVOLVE_PROJECT)
        );
        return Map.copyOf(handlers);
    }

    public Set<String> registeredCommandIds() {
        return handlersByCommandId.keySet();
    }

    public RuntimeCommandExecutionResult execute(CommandInvocation invocation) {
        CommandInvocation input = Objects.requireNonNull(invocation, "invocation");
        String commandId = normalize(input.commandId());
        RuntimeCommandHandler handler = handlersByCommandId.get(commandId);
        if (handler == null) {
            return RuntimeCommandExecutionResult.denied(
                    commandId,
                    "command_not_registered",
                    Map.of("schema", "eclipse_runtime_context_preview_v1"),
                    policyFallbackUrl(commandId)
            );
        }

        EclipseContextCaptureRuntime.BoundedContextPayload boundedContext = contextCaptureRuntime.capture(
                input.workspaceState(),
                input.editorState()
        );
        Map<String, Object> contextPreview = boundedContext.toPreviewMap();

        CapabilityGate.GateDecision gateDecision = capabilityGate.evaluate(
                commandId,
                handler.commandType().requiredCapability()
        );
        if (!gateDecision.allowed()) {
            return RuntimeCommandExecutionResult.denied(
                    commandId,
                    gateDecision.reason(),
                    contextPreview,
                    policyFallbackUrl(commandId)
            );
        }

        RuntimeCommandPayload payload = new RuntimeCommandPayload(
                normalizeGoalText(input.goalText(), handler.commandType()),
                boundedContext.toContextJson(),
                normalize(input.operationPreset()).isBlank() ? handler.commandType().operationPreset() : normalize(input.operationPreset()),
                normalize(input.profileId()),
                normalize(input.blueprintId()),
                normalize(input.workProfileId())
        );
        ClientResponse response = handler.execute(apiClient, payload);
        String browserFallbackUrl = response.getState().name().equals("POLICY_DENIED")
                ? policyFallbackUrl(commandId)
                : null;
        return RuntimeCommandExecutionResult.executed(commandId, response, contextPreview, browserFallbackUrl);
    }

    public GoalSubmissionRuntimePanel.GoalSubmissionResult submitGoalFromPanel(CommandInvocation invocation) {
        CommandInvocation input = Objects.requireNonNull(invocation, "invocation");
        EclipseContextCaptureRuntime.BoundedContextPayload boundedContext = contextCaptureRuntime.capture(
                input.workspaceState(),
                input.editorState()
        );
        RuntimeCommandType commandType = RuntimeCommandType.fromCommandId(input.commandId()).orElse(RuntimeCommandType.ANALYZE);
        GoalSubmissionRuntimePanel.GoalSubmissionPreview preview = goalSubmissionRuntimePanel.buildPreview(
                normalizeGoalText(input.goalText(), commandType),
                normalize(input.operationPreset()).isBlank() ? commandType.operationPreset() : normalize(input.operationPreset()),
                normalize(input.profileId()),
                boundedContext.toPreviewMap()
        );
        return goalSubmissionRuntimePanel.submit(preview, boundedContext.toContextJson(), commandType.commandId());
    }

    private static String normalizeGoalText(String goalText, RuntimeCommandType commandType) {
        String normalized = normalize(goalText);
        if (!normalized.isBlank()) {
            return normalized;
        }
        return switch (commandType) {
            case ANALYZE -> "Analyze current workspace context";
            case REVIEW -> "Review selected code changes";
            case PATCH -> "Create patch plan for selected context";
            case NEW_PROJECT -> "Create a new software project";
            case EVOLVE_PROJECT -> "Evolve existing software project";
        };
    }

    private static String normalize(String value) {
        return Objects.toString(value, "").trim();
    }

    private static String policyFallbackUrl(String commandId) {
        String encoded = URLEncoder.encode(Objects.toString(commandId, "").trim(), StandardCharsets.UTF_8);
        return "/governance/policy-denied?command_id=" + encoded;
    }

    public record CommandInvocation(
            String commandId,
            String goalText,
            String operationPreset,
            String profileId,
            String blueprintId,
            String workProfileId,
            EclipseContextCaptureRuntime.WorkspaceState workspaceState,
            EclipseContextCaptureRuntime.EditorState editorState
    ) {
    }
}
