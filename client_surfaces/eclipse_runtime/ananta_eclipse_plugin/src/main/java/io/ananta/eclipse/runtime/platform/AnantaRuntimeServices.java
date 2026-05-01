package io.ananta.eclipse.runtime.platform;

import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;

import java.util.Objects;

public final class AnantaRuntimeServices {
    private final AnantaApiClient apiClient;
    private final CapabilityGate capabilityGate;
    private final EclipseContextCaptureRuntime contextCaptureRuntime;

    public AnantaRuntimeServices(
            AnantaApiClient apiClient,
            CapabilityGate capabilityGate,
            EclipseContextCaptureRuntime contextCaptureRuntime
    ) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
        this.capabilityGate = Objects.requireNonNull(capabilityGate, "capabilityGate");
        this.contextCaptureRuntime = Objects.requireNonNull(contextCaptureRuntime, "contextCaptureRuntime");
    }

    public AnantaApiClient apiClient() {
        return apiClient;
    }

    public CapabilityGate capabilityGate() {
        return capabilityGate;
    }

    public EclipseContextCaptureRuntime contextCaptureRuntime() {
        return contextCaptureRuntime;
    }
}
