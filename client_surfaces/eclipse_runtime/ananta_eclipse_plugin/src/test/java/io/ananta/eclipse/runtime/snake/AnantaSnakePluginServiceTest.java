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
            assertEquals("follow_mouse", tracked.getFollowMode());

            assertThrows(IllegalArgumentException.class, () -> service.setFollowMode("warp"));
            assertThrows(IllegalArgumentException.class, () -> service.setContextMode("unknown"));
            assertThrows(IllegalArgumentException.class, () -> service.setHubConnectionState("cloud_auto"));
        } finally {
            service.shutdown();
        }
    }

    @Test
    void followAlgorithmKeepsDistanceAndInterpolatesDeterministically() {
        AnantaSnakePluginService service = new AnantaSnakePluginService();
        try {
            service.start();
            service.setFollowDistancePx(30);
            service.updateMousePosition(
                    new AnantaMouseTrackingRuntime.Point(240, 120),
                    new AnantaMouseTrackingRuntime.Bounds(0, 0, 300, 200),
                    new AnantaMouseTrackingRuntime.Bounds(0, 0, 300, 200)
            );
            AnantaSnakeState before = service.snapshot();
            AnantaSnakeState afterFirstTick = service.tickNowForTest();
            AnantaSnakeState afterSecondTick = service.tickNowForTest();

            assertTrue(afterFirstTick.getOverlayX() > before.getOverlayX());
            assertTrue(afterFirstTick.getOverlayY() > before.getOverlayY());
            assertTrue(afterSecondTick.getOverlayX() >= afterFirstTick.getOverlayX());
            assertTrue(afterSecondTick.getOverlayY() >= afterFirstTick.getOverlayY());

            int distanceToMouse = (int) Math.round(Math.hypot(
                    afterSecondTick.getMouseX() - afterSecondTick.getOverlayX(),
                    afterSecondTick.getMouseY() - afterSecondTick.getOverlayY()
            ));
            assertTrue(distanceToMouse >= 20);
            assertEquals("follow_mouse", afterSecondTick.getFollowMode());
        } finally {
            service.shutdown();
        }
    }

    @Test
    void stillMouseSwitchesToLurkingAndResumesFollowOnMovement() {
        AnantaSnakePluginService service = new AnantaSnakePluginService();
        try {
            service.start();
            service.recordActiveWorkbenchPart("org.eclipse.ui.editors", "Main.java");
            for (int idx = 0; idx < 4; idx++) {
                service.updateMousePosition(
                        new AnantaMouseTrackingRuntime.Point(80, 40),
                        new AnantaMouseTrackingRuntime.Bounds(0, 0, 100, 100),
                        new AnantaMouseTrackingRuntime.Bounds(0, 0, 100, 100)
                );
            }
            AnantaSnakeState lurking = service.snapshot();
            assertEquals("lurking", lurking.getFollowMode());
            assertEquals("editor_focus", lurking.getContextMode());

            service.updateMousePosition(
                    new AnantaMouseTrackingRuntime.Point(120, 60),
                    new AnantaMouseTrackingRuntime.Bounds(0, 0, 200, 200),
                    new AnantaMouseTrackingRuntime.Bounds(0, 0, 200, 200)
            );
            AnantaSnakeState following = service.snapshot();
            assertEquals("follow_mouse", following.getFollowMode());
        } finally {
            service.shutdown();
        }
    }

    @Test
    void performanceBudgetUsesConfigurableTickRateAndReducesWhenWorkbenchInactive() {
        AnantaSnakePluginService service = new AnantaSnakePluginService();
        try {
            service.start();
            AnantaSnakeState clampedHigh = service.setTickRateFps(200);
            assertEquals(30, clampedHigh.getTickRateFps());

            AnantaSnakeState clampedLow = service.setTickRateFps(1);
            assertEquals(15, clampedLow.getTickRateFps());

            AnantaSnakeState inactive = service.setWorkbenchActive(false);
            assertFalse(inactive.isWorkbenchActive());
            assertEquals(5, inactive.getTickRateFps());

            AnantaSnakeState active = service.setWorkbenchActive(true);
            assertTrue(active.isWorkbenchActive());
            assertEquals(15, active.getTickRateFps());
        } finally {
            service.shutdown();
        }
    }
}
