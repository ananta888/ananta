package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class AnantaMouseTrackingRuntimeTest {
    @Test
    void coordinateNormalizationMapsBetweenSourceAndOverlaySpaces() {
        AnantaMouseTrackingRuntime runtime = new AnantaMouseTrackingRuntime();
        AnantaMouseTrackingRuntime.Point normalized = runtime.normalizePoint(
                new AnantaMouseTrackingRuntime.Point(50, 25),
                new AnantaMouseTrackingRuntime.Bounds(0, 0, 100, 50),
                new AnantaMouseTrackingRuntime.Bounds(10, 10, 200, 100)
        );
        assertEquals(110, normalized.x());
        assertEquals(60, normalized.y());
    }

    @Test
    void coordinateNormalizationClampsOutOfBoundsValues() {
        AnantaMouseTrackingRuntime runtime = new AnantaMouseTrackingRuntime();
        AnantaMouseTrackingRuntime.Point normalized = runtime.normalizePoint(
                new AnantaMouseTrackingRuntime.Point(-200, 999),
                new AnantaMouseTrackingRuntime.Bounds(0, 0, 100, 100),
                new AnantaMouseTrackingRuntime.Bounds(20, 30, 80, 40)
        );
        assertEquals(20, normalized.x());
        assertEquals(70, normalized.y());
    }

    @Test
    void boundsRejectNonPositiveSize() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new AnantaMouseTrackingRuntime.Bounds(0, 0, 0, 10)
        );
        assertThrows(
                IllegalArgumentException.class,
                () -> new AnantaMouseTrackingRuntime.Bounds(0, 0, 10, -1)
        );
    }
}
