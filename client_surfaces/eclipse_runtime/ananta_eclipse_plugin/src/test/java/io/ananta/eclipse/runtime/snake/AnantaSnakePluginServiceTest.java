package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaSnakePluginServiceTest {
    @Test
    void startStopRestartAndToggleRemainDeterministic() {
        AnantaSnakePluginService service = new AnantaSnakePluginService();
        try {
            AnantaSnakeState initial = service.snapshot();
            assertFalse(initial.isEnabled());
            assertFalse(initial.isRunning());

            AnantaSnakeState started = service.start();
            assertTrue(started.isEnabled());
            assertTrue(started.isRunning());
            assertTrue(started.isOverlayVisible());

            AnantaSnakeState stopped = service.stop();
            assertFalse(stopped.isRunning());
            assertFalse(stopped.isOverlayVisible());

            AnantaSnakeState restarted = service.restart();
            assertTrue(restarted.isEnabled());
            assertTrue(restarted.isRunning());

            AnantaSnakeState toggledOff = service.toggleEnabled();
            assertFalse(toggledOff.isEnabled());
            assertFalse(toggledOff.isRunning());

            AnantaSnakeState toggledOn = service.toggleEnabled();
            assertTrue(toggledOn.isEnabled());
            assertTrue(toggledOn.isRunning());
        } finally {
            service.shutdown();
        }
    }

    @Test
    void modesAndMouseTrackingRequireKnownValues() {
        AnantaSnakePluginService service = new AnantaSnakePluginService();
        try {
            service.start();
            AnantaSnakeState lurking = service.setFollowMode("lurking");
            assertEquals("lurking", lurking.getFollowMode());

            AnantaSnakeState editor = service.setContextMode("editor_focus");
            assertEquals("editor_focus", editor.getContextMode());

            AnantaSnakeState local = service.setHubConnectionState("local_only");
            assertEquals("local_only", local.getHubConnectionState());

            AnantaSnakeState tracked = service.updateMousePosition(
                    new AnantaMouseTrackingRuntime.Point(110, 55),
                    new AnantaMouseTrackingRuntime.Bounds(0, 0, 200, 100),
                    new AnantaMouseTrackingRuntime.Bounds(10, 10, 300, 200)
            );
            assertEquals(110, tracked.getMouseX());
            assertEquals(55, tracked.getMouseY());
            assertTrue(tracked.getOverlayX() >= 10);
            assertTrue(tracked.getOverlayY() >= 10);

            assertThrows(IllegalArgumentException.class, () -> service.setFollowMode("warp"));
            assertThrows(IllegalArgumentException.class, () -> service.setContextMode("unknown"));
            assertThrows(IllegalArgumentException.class, () -> service.setHubConnectionState("cloud_auto"));
        } finally {
            service.shutdown();
        }
    }
}
