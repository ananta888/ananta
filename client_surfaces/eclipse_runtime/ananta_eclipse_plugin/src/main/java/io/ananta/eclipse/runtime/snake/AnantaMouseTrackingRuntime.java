package io.ananta.eclipse.runtime.snake;

import java.util.Objects;

public final class AnantaMouseTrackingRuntime {
    public record Point(int x, int y) {
    }

    public record Bounds(int left, int top, int width, int height) {
        public Bounds {
            if (width <= 0 || height <= 0) {
                throw new IllegalArgumentException("bounds_size_must_be_positive");
            }
        }
    }

    public Point normalizePoint(Point mousePoint, Bounds sourceBounds, Bounds overlayBounds) {
        Point raw = Objects.requireNonNull(mousePoint, "mousePoint");
        Bounds source = Objects.requireNonNull(sourceBounds, "sourceBounds");
        Bounds overlay = Objects.requireNonNull(overlayBounds, "overlayBounds");

        double xRatio = ((double) raw.x() - source.left()) / source.width();
        double yRatio = ((double) raw.y() - source.top()) / source.height();
        int normalizedX = overlay.left() + (int) Math.round(xRatio * overlay.width());
        int normalizedY = overlay.top() + (int) Math.round(yRatio * overlay.height());

        int clampedX = clamp(normalizedX, overlay.left(), overlay.left() + overlay.width());
        int clampedY = clamp(normalizedY, overlay.top(), overlay.top() + overlay.height());
        return new Point(clampedX, clampedY);
    }

    private static int clamp(int value, int min, int max) {
        if (value < min) {
            return min;
        }
        return Math.min(value, max);
    }
}
