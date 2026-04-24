package io.ananta.eclipse.runtime.core;

public final class ClientResponse {
    private final boolean ok;
    private final Integer statusCode;
    private final DegradedState state;
    private final String responseBody;
    private final String error;
    private final boolean retriable;

    public ClientResponse(
            boolean ok,
            Integer statusCode,
            DegradedState state,
            String responseBody,
            String error,
            boolean retriable
    ) {
        this.ok = ok;
        this.statusCode = statusCode;
        this.state = state;
        this.responseBody = responseBody;
        this.error = error;
        this.retriable = retriable;
    }

    public boolean isOk() {
        return ok;
    }

    public Integer getStatusCode() {
        return statusCode;
    }

    public DegradedState getState() {
        return state;
    }

    public String getResponseBody() {
        return responseBody;
    }

    public String getError() {
        return error;
    }

    public boolean isRetriable() {
        return retriable;
    }
}
