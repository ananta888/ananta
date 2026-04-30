package com.ananta.mobile.security;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Default-deny broker for sensitive mobile runtime actions.
 */
public final class PermissionBroker {
    private static final Set<String> ALLOWED_ACTIONS = new HashSet<>(Arrays.asList(
            "download_model",
            "download_runner",
            "transcribe",
            "start_live"
    ));

    public boolean allows(String action, boolean userConfirmed) {
        String normalized = action == null ? "" : action.trim().toLowerCase(Locale.ROOT);
        if (!ALLOWED_ACTIONS.contains(normalized)) return false;
        return userConfirmed;
    }
}
