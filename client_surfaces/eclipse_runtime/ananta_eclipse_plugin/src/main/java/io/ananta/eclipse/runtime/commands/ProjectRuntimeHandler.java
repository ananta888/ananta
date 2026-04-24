package io.ananta.eclipse.runtime.commands;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

public final class ProjectRuntimeHandler implements RuntimeCommandHandler {
    private final RuntimeCommandType commandType;

    public ProjectRuntimeHandler(RuntimeCommandType commandType) {
        if (commandType != RuntimeCommandType.NEW_PROJECT && commandType != RuntimeCommandType.EVOLVE_PROJECT) {
            throw new IllegalArgumentException("handler only supports new/evolve project flows");
        }
        this.commandType = commandType;
    }

    @Override
    public RuntimeCommandType commandType() {
        return commandType;
    }

    @Override
    public ClientResponse execute(AnantaApiClient apiClient, RuntimeCommandPayload payload) {
        String goalText = payload.goalText();
        if (commandType == RuntimeCommandType.NEW_PROJECT) {
            return apiClient.createProjectNew(
                    goalText,
                    payload.boundedContextJson(),
                    payload.blueprintId(),
                    payload.workProfileId()
            );
        }
        return apiClient.createProjectEvolve(
                goalText,
                payload.boundedContextJson(),
                payload.blueprintId(),
                payload.workProfileId()
        );
    }
}
