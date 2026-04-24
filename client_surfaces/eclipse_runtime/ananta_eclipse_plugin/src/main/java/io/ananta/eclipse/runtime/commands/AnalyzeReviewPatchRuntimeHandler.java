package io.ananta.eclipse.runtime.commands;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

public final class AnalyzeReviewPatchRuntimeHandler implements RuntimeCommandHandler {
    private final RuntimeCommandType commandType;

    public AnalyzeReviewPatchRuntimeHandler(RuntimeCommandType commandType) {
        if (commandType != RuntimeCommandType.ANALYZE
                && commandType != RuntimeCommandType.REVIEW
                && commandType != RuntimeCommandType.PATCH) {
            throw new IllegalArgumentException("handler only supports analyze/review/patch");
        }
        this.commandType = commandType;
    }

    @Override
    public RuntimeCommandType commandType() {
        return commandType;
    }

    @Override
    public ClientResponse execute(AnantaApiClient apiClient, RuntimeCommandPayload payload) {
        return switch (commandType) {
            case ANALYZE -> apiClient.analyzeContext(payload.boundedContextJson());
            case REVIEW -> apiClient.reviewContext(payload.boundedContextJson());
            case PATCH -> apiClient.patchPlan(payload.boundedContextJson());
            default -> throw new IllegalStateException("unsupported command type for analyze/review/patch handler");
        };
    }
}
