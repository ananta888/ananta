package io.ananta.eclipse.runtime.snake;

public record AnantaSnakePrivacySettings(
        boolean allowSelectionContent,
        boolean allowFileContent,
        boolean allowExternalProviders
) {
    public static AnantaSnakePrivacySettings safeDefaults() {
        return new AnantaSnakePrivacySettings(false, false, false);
    }

    public AnantaSnakePrivacySettings {
        if (!allowSelectionContent && allowFileContent) {
            throw new IllegalArgumentException("file_content_requires_selection_consent");
        }
    }
}
