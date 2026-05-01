package io.ananta.eclipse.runtime.preferences;

import io.ananta.eclipse.runtime.core.ClientProfile;

import org.eclipse.core.runtime.preferences.IEclipsePreferences;
import org.eclipse.core.runtime.preferences.InstanceScope;
import org.eclipse.equinox.security.storage.ISecurePreferences;
import org.eclipse.equinox.security.storage.SecurePreferencesFactory;
import org.eclipse.equinox.security.storage.StorageException;
import org.osgi.service.prefs.BackingStoreException;

import java.util.Objects;

public final class AnantaPreferenceRuntimeStore {
    private static final String PREF_NODE = "io.ananta.eclipse.runtime";
    private static final String SECURE_NODE = "io.ananta.eclipse.runtime.profiles";

    private static final String KEY_PROFILE_ID = "profile_id";
    private static final String KEY_BASE_URL = "base_url";
    private static final String KEY_AUTH_MODE = "auth_mode";
    private static final String KEY_ENVIRONMENT = "environment";
    private static final String KEY_TIMEOUT_SECONDS = "timeout_seconds";
    private static final String KEY_AUTH_TOKEN = "auth_token";

    private AnantaPreferenceRuntimeStore() {
    }

    public static ClientProfile loadProfile() {
        IEclipsePreferences node = InstanceScope.INSTANCE.getNode(PREF_NODE);
        String profileId = node.get(KEY_PROFILE_ID, "default");
        String baseUrl = node.get(KEY_BASE_URL, "http://localhost:8080");
        String authMode = node.get(KEY_AUTH_MODE, "session_token");
        String environment = node.get(KEY_ENVIRONMENT, "local");
        int timeoutSeconds = node.getInt(KEY_TIMEOUT_SECONDS, 15);
        String token = loadToken(profileId);
        return new ClientProfile(profileId, baseUrl, authMode, environment, token, timeoutSeconds);
    }

    public static void saveProfile(ClientProfile profile) {
        ClientProfile input = Objects.requireNonNull(profile, "profile");
        IEclipsePreferences node = InstanceScope.INSTANCE.getNode(PREF_NODE);
        node.put(KEY_PROFILE_ID, input.getProfileId());
        node.put(KEY_BASE_URL, input.getBaseUrl());
        node.put(KEY_AUTH_MODE, input.getAuthMode());
        node.put(KEY_ENVIRONMENT, input.getEnvironment());
        node.putInt(KEY_TIMEOUT_SECONDS, input.getTimeoutSeconds());
        try {
            node.flush();
        } catch (BackingStoreException exc) {
            throw new IllegalStateException("failed_to_store_profile_preferences", exc);
        }
        saveToken(input.getProfileId(), input.getAuthToken());
    }

    private static String loadToken(String profileId) {
        String normalized = normalizeProfileId(profileId);
        ISecurePreferences root = SecurePreferencesFactory.getDefault();
        ISecurePreferences node = root.node(SECURE_NODE).node(normalized);
        try {
            return node.get(KEY_AUTH_TOKEN, "");
        } catch (StorageException exc) {
            throw new IllegalStateException("failed_to_load_secure_token", exc);
        }
    }

    private static void saveToken(String profileId, String token) {
        String normalized = normalizeProfileId(profileId);
        ISecurePreferences root = SecurePreferencesFactory.getDefault();
        ISecurePreferences node = root.node(SECURE_NODE).node(normalized);
        String normalizedToken = Objects.toString(token, "").trim();
        try {
            if (normalizedToken.isBlank()) {
                node.remove(KEY_AUTH_TOKEN);
                return;
            }
            node.put(KEY_AUTH_TOKEN, normalizedToken, true);
        } catch (StorageException exc) {
            throw new IllegalStateException("failed_to_store_secure_token", exc);
        }
    }

    private static String normalizeProfileId(String value) {
        String profileId = Objects.toString(value, "").trim();
        return profileId.isBlank() ? "default" : profileId;
    }
}
