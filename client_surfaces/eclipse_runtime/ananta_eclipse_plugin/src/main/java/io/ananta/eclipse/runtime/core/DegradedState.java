package io.ananta.eclipse.runtime.core;

public enum DegradedState {
    HEALTHY,
    BACKEND_UNREACHABLE,
    AUTH_FAILED,
    CAPABILITY_MISSING,
    POLICY_DENIED,
    MALFORMED_RESPONSE,
    UNKNOWN_ERROR
}
