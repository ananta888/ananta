package io.ananta.eclipse.runtime.repair;

import java.util.List;
import java.util.Objects;

public final class ProblemMarkerContextCapture {
    public MarkerContext capture(List<ProblemMarker> markers, int maxMarkers) {
        int limit = Math.max(1, maxMarkers);
        List<ProblemMarker> bounded = (markers == null ? List.<ProblemMarker>of() : markers).stream()
                .limit(limit)
                .toList();
        return new MarkerContext(bounded, markers != null && markers.size() > limit, true);
    }

    public record ProblemMarker(String project, String path, int line, String severity, String message) {
        public ProblemMarker {
            project = Objects.toString(project, "").trim();
            path = Objects.toString(path, "").trim();
            severity = Objects.toString(severity, "").trim();
            message = Objects.toString(message, "").trim();
        }
    }

    public record MarkerContext(List<ProblemMarker> markers, boolean clipped, boolean bounded) {
        public MarkerContext {
            markers = markers == null ? List.of() : List.copyOf(markers);
        }
    }
}
