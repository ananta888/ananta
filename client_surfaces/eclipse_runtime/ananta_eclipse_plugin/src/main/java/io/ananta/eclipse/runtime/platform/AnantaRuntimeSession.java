package io.ananta.eclipse.runtime.platform;

import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.core.DegradedState;

import java.util.Objects;

public final class AnantaRuntimeSession {
    private final AnantaRuntimeServices services;
    private RuntimeUiState uiState = RuntimeUiState.from(DegradedState.UNKNOWN_ERROR, "not_connected");

    public AnantaRuntimeSession(AnantaRuntimeServices services) {
        this.services = Objects.requireNonNull(services, "services");
    }

    public AnantaRuntimeServices services() {
        return services;
    }

    public RuntimeUiState refreshHealth() {
        ClientResponse response = services.apiClient().getHealth();
        uiState = RuntimeUiState.from(response.getState(), response.isOk() ? "connected" : response.getError());
        return uiState;
    }

    public RuntimeUiState uiState() {
        return uiState;
    }
}
