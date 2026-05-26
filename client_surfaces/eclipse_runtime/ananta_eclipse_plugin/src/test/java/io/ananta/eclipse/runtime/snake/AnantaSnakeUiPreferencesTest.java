package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaSnakeUiPreferencesTest {
    @Test
    void defaultsStayPrivacyFirstAndLocalOnly() {
        AnantaSnakeUiPreferences defaults = AnantaSnakeUiPreferences.defaults();
        assertFalse(defaults.snakeEnabledByDefault());
        assertTrue(defaults.localOnlyMode());
        assertFalse(defaults.doNotDisturbMode());
        assertFalse(defaults.privacySettings().allowSelectionContent());
        assertFalse(defaults.privacySettings().allowFileContent());
        assertFalse(defaults.privacySettings().allowExternalProviders());
    }

    @Test
    void valuesAreClampedToSafeRanges() {
        AnantaSnakeUiPreferences preferences = new AnantaSnakeUiPreferences(
                true,
                100,
                300,
                2,
                false,
                false,
                new AnantaSnakePrivacySettings(true, true, true)
        );
        assertEquals(30, preferences.animationFps());
        assertEquals(120, preferences.followDistancePx());
        assertEquals(10, preferences.overlayOpacityPercent());
    }

    @Test
    void fileContentRequiresSelectionConsent() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new AnantaSnakePrivacySettings(false, true, false)
        );
    }
}
