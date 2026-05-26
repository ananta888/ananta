package io.ananta.eclipse.runtime.preferences;

import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.snake.AnantaSnakePrivacySettings;
import io.ananta.eclipse.runtime.snake.AnantaSnakeUiPreferences;

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
    private static final String KEY_SNAKE_HUB_ENABLED = "snake_hub_enabled";
    private static final String KEY_SNAKE_ENABLED = "snake_enabled";
    private static final String KEY_SNAKE_ANIMATION_FPS = "snake_animation_fps";
    private static final String KEY_SNAKE_FOLLOW_DISTANCE_PX = "snake_follow_distance_px";
    private static final String KEY_SNAKE_OVERLAY_OPACITY_PERCENT = "snake_overlay_opacity_percent";
    private static final String KEY_SNAKE_LOCAL_ONLY_MODE = "snake_local_only_mode";
    private static final String KEY_SNAKE_ALLOW_SELECTION_CONTENT = "snake_allow_selection_content";
    private static final String KEY_SNAKE_ALLOW_FILE_CONTENT = "snake_allow_file_content";
    private static final String KEY_SNAKE_ALLOW_EXTERNAL_PROVIDERS = "snake_allow_external_providers";

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

    public static boolean loadSnakeHubEnabled() {
        IEclipsePreferences node = InstanceScope.INSTANCE.getNode(PREF_NODE);
        return node.getBoolean(KEY_SNAKE_HUB_ENABLED, false);
    }

    public static void saveSnakeHubEnabled(boolean enabled) {
        IEclipsePreferences node = InstanceScope.INSTANCE.getNode(PREF_NODE);
        node.putBoolean(KEY_SNAKE_HUB_ENABLED, enabled);
        try {
            node.flush();
        } catch (BackingStoreException exc) {
            throw new IllegalStateException("failed_to_store_snake_hub_enabled", exc);
        }
    }

    public static AnantaSnakeUiPreferences loadSnakeUiPreferences() {
        IEclipsePreferences node = InstanceScope.INSTANCE.getNode(PREF_NODE);
        return new AnantaSnakeUiPreferences(
                node.getBoolean(KEY_SNAKE_ENABLED, AnantaSnakeUiPreferences.defaults().snakeEnabledByDefault()),
                node.getInt(KEY_SNAKE_ANIMATION_FPS, AnantaSnakeUiPreferences.defaults().animationFps()),
                node.getInt(KEY_SNAKE_FOLLOW_DISTANCE_PX, AnantaSnakeUiPreferences.defaults().followDistancePx()),
                node.getInt(KEY_SNAKE_OVERLAY_OPACITY_PERCENT, AnantaSnakeUiPreferences.defaults().overlayOpacityPercent()),
                node.getBoolean(KEY_SNAKE_LOCAL_ONLY_MODE, AnantaSnakeUiPreferences.defaults().localOnlyMode()),
                new AnantaSnakePrivacySettings(
                        node.getBoolean(
                                KEY_SNAKE_ALLOW_SELECTION_CONTENT,
                                AnantaSnakeUiPreferences.defaults().privacySettings().allowSelectionContent()
                        ),
                        node.getBoolean(
                                KEY_SNAKE_ALLOW_FILE_CONTENT,
                                AnantaSnakeUiPreferences.defaults().privacySettings().allowFileContent()
                        ),
                        node.getBoolean(
                                KEY_SNAKE_ALLOW_EXTERNAL_PROVIDERS,
                                AnantaSnakeUiPreferences.defaults().privacySettings().allowExternalProviders()
                        )
                )
        );
    }

    public static void saveSnakeUiPreferences(AnantaSnakeUiPreferences preferences) {
        AnantaSnakeUiPreferences input = preferences == null ? AnantaSnakeUiPreferences.defaults() : preferences;
        IEclipsePreferences node = InstanceScope.INSTANCE.getNode(PREF_NODE);
        node.putBoolean(KEY_SNAKE_ENABLED, input.snakeEnabledByDefault());
        node.putInt(KEY_SNAKE_ANIMATION_FPS, input.animationFps());
        node.putInt(KEY_SNAKE_FOLLOW_DISTANCE_PX, input.followDistancePx());
        node.putInt(KEY_SNAKE_OVERLAY_OPACITY_PERCENT, input.overlayOpacityPercent());
        node.putBoolean(KEY_SNAKE_LOCAL_ONLY_MODE, input.localOnlyMode());
        node.putBoolean(KEY_SNAKE_ALLOW_SELECTION_CONTENT, input.privacySettings().allowSelectionContent());
        node.putBoolean(KEY_SNAKE_ALLOW_FILE_CONTENT, input.privacySettings().allowFileContent());
        node.putBoolean(KEY_SNAKE_ALLOW_EXTERNAL_PROVIDERS, input.privacySettings().allowExternalProviders());
        try {
            node.flush();
        } catch (BackingStoreException exc) {
            throw new IllegalStateException("failed_to_store_snake_ui_preferences", exc);
        }
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
