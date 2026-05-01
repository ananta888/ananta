package io.ananta.eclipse.runtime.platform;

import io.ananta.eclipse.runtime.core.DegradedState;

import java.util.Objects;

public final class RuntimeUiState {
    private final DegradedState state;
    private final String message;
    private final boolean actionsEnabled;
    private final boolean retryVisible;

    private RuntimeUiState(DegradedState state, String message, boolean actionsEnabled, boolean retryVisible) {
        this.state = Objects.requireNonNull(state, "state");
        this.message = Objects.toString(message, "").trim();
        this.actionsEnabled = actionsEnabled;
        this.retryVisible = retryVisible;
    }

    public static RuntimeUiState from(DegradedState state, String message) {
        DegradedState normalized = state == null ? DegradedState.UNKNOWN_ERROR : state;
        boolean healthy = normalized == DegradedState.HEALTHY;
        boolean retry = normalized == DegradedState.BACKEND_UNREACHABLE
                || normalized == DegradedState.MALFORMED_RESPONSE
                || normalized == DegradedState.UNKNOWN_ERROR;
        return new RuntimeUiState(normalized, message, healthy, retry);
    }

    public DegradedState getState() {
        return state;
    }

    public String getMessage() {
        return message;
    }

    public boolean isActionsEnabled() {
        return actionsEnabled;
    }

    public boolean isRetryVisible() {
        return retryVisible;
    }
}
