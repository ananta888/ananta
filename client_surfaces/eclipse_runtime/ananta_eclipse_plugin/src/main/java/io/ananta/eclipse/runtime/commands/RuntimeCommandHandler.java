package io.ananta.eclipse.runtime.commands;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

public interface RuntimeCommandHandler {
    RuntimeCommandType commandType();

    ClientResponse execute(AnantaApiClient apiClient, RuntimeCommandPayload payload);
}
