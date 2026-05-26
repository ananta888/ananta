package io.ananta.eclipse.runtime.snake;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public final class AnantaSnakeOverlayModel {
    public record Segment(int x, int y) {
    }

    private final List<Segment> segments;

    public AnantaSnakeOverlayModel(List<Segment> segments) {
        if (segments == null || segments.isEmpty()) {
            throw new IllegalArgumentException("snake_segments_required");
        }
        this.segments = List.copyOf(segments);
    }

    public static AnantaSnakeOverlayModel fromState(AnantaSnakeState state) {
        if (state == null) {
            return new AnantaSnakeOverlayModel(List.of(new Segment(0, 0)));
        }
        List<Segment> points = new ArrayList<>();
        int headX = state.getOverlayX();
        int headY = state.getOverlayY();
        points.add(new Segment(headX, headY));
        int trailLength = 6;
        for (int idx = 1; idx <= trailLength; idx++) {
            points.add(new Segment(headX - (idx * 6), headY + (idx % 2 == 0 ? 2 : -2)));
        }
        return new AnantaSnakeOverlayModel(points);
    }

    public List<Segment> segments() {
        return Collections.unmodifiableList(segments);
    }
}
