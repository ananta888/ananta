package io.ananta.eclipse.runtime.snake;

public record AnantaSnakeUiPreferences(
        boolean snakeEnabledByDefault,
        int animationFps,
        int followDistancePx,
        int overlayOpacityPercent,
        boolean localOnlyMode,
        boolean doNotDisturbMode,
        AnantaSnakePrivacySettings privacySettings
) {
    public AnantaSnakeUiPreferences {
        animationFps = Math.max(15, Math.min(30, animationFps));
        followDistancePx = Math.max(4, Math.min(120, followDistancePx));
        overlayOpacityPercent = Math.max(10, Math.min(100, overlayOpacityPercent));
        privacySettings = privacySettings == null ? AnantaSnakePrivacySettings.safeDefaults() : privacySettings;
    }

    public static AnantaSnakeUiPreferences defaults() {
        return new AnantaSnakeUiPreferences(
                false,
                20,
                24,
                60,
                true,
                false,
                AnantaSnakePrivacySettings.safeDefaults()
        );
    }
}
