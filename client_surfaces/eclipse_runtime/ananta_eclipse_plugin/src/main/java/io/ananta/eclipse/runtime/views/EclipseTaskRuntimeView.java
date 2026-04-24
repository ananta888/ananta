package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.Objects;

public final class EclipseTaskRuntimeView {
    private final AnantaApiClient apiClient;

    public EclipseTaskRuntimeView(AnantaApiClient apiClient) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
    }

    public TaskViewModel loadTaskViews(String selectedTaskId) {
        ClientResponse taskListResponse = apiClient.listTasks();
        ClientResponse taskDetailResponse = null;
        String normalizedTaskId = Objects.toString(selectedTaskId, "").trim();
        if (!normalizedTaskId.isBlank()) {
            taskDetailResponse = apiClient.getTask(normalizedTaskId);
        }
        boolean staleOrMissingState = !taskListResponse.isOk()
                || (taskDetailResponse != null && !taskDetailResponse.isOk());
        return new TaskViewModel(
                taskListResponse,
                taskDetailResponse,
                true,
                staleOrMissingState,
                true,
                true,
                true
        );
    }

    public record TaskViewModel(
            ClientResponse taskListResponse,
            ClientResponse taskDetailResponse,
            boolean refreshControlsVisible,
            boolean staleOrMissingState,
            boolean statusMarkerVisible,
            boolean reviewRequiredMarkerVisible,
            boolean nextStepMarkerVisible
    ) {
    }
}
