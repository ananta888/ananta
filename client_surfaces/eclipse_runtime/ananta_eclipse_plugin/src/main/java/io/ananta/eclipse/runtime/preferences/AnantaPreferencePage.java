package io.ananta.eclipse.runtime.preferences;

import io.ananta.eclipse.runtime.core.ClientProfile;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

public final class AnantaPreferencePage {
    public PreferenceValidation validate(ProfilePreferenceDraft draft) {
        try {
            ClientProfile profile = draft.toProfile();
            return new PreferenceValidation(true, "", profile.toPersistenceMap());
        } catch (IllegalArgumentException exc) {
            return new PreferenceValidation(false, Objects.toString(exc.getMessage(), "invalid_profile"), Map.of());
        }
    }

    public record ProfilePreferenceDraft(
            String profileId,
            String baseUrl,
            String authMode,
            String environment,
            String token,
            int timeoutSeconds
    ) {
        public ClientProfile toProfile() {
            return new ClientProfile(profileId, baseUrl, authMode, environment, token, timeoutSeconds);
        }
    }

    public record PreferenceValidation(boolean valid, String error, Map<String, String> persistedProfile) {
        public PreferenceValidation {
            persistedProfile = persistedProfile == null ? Map.of() : Map.copyOf(new LinkedHashMap<>(persistedProfile));
        }
    }
}
