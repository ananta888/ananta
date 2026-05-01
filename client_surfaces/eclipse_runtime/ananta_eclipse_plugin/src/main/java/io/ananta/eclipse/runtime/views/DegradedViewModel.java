package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.DegradedState;

public final class DegradedViewModel {
    public ViewState from(DegradedState state, String message) {
        DegradedState normalized = state == null ? DegradedState.UNKNOWN_ERROR : state;
        boolean retry = normalized == DegradedState.BACKEND_UNREACHABLE
                || normalized == DegradedState.MALFORMED_RESPONSE
                || normalized == DegradedState.UNKNOWN_ERROR;
        return new ViewState(
                normalized.name().toLowerCase(),
                message == null ? "" : message,
                retry,
                normalized == DegradedState.HEALTHY
        );
    }

    public record ViewState(String state, String message, boolean retryVisible, boolean contentEnabled) {
    }
}
