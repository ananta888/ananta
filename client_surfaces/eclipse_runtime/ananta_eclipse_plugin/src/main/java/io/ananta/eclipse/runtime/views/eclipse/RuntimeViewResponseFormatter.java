package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.security.TokenRedaction;

import java.util.Objects;

final class RuntimeViewResponseFormatter {
    private RuntimeViewResponseFormatter() {
    }

    static String block(String title, ClientResponse response) {
        if (response == null) {
            return title + ": not_requested";
        }
        String body = TokenRedaction.redactSensitiveText(Objects.toString(response.getResponseBody(), ""));
        if (body.length() > 4000) {
            body = body.substring(0, 4000) + "\n... (truncated)";
        }
        return title + ": state=" + response.getState().name().toLowerCase()
                + ", status=" + response.getStatusCode()
                + "\n" + body;
    }
}
