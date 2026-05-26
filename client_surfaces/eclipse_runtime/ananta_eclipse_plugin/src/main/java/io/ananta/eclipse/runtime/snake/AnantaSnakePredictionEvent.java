package io.ananta.eclipse.runtime.snake;

import java.util.List;

public record AnantaSnakePredictionEvent(
        String intentKind,
        double confidence,
        List<String> evidence,
        long expiresAtEpochMillis
) {
    public AnantaSnakePredictionEvent {
        evidence = evidence == null ? List.of() : List.copyOf(evidence);
    }

    public static AnantaSnakePredictionEvent unknown(long nowEpochMillis) {
        return new AnantaSnakePredictionEvent(
                "unknown",
                0.2,
                List.of("insufficient_context"),
                nowEpochMillis + 10_000L
        );
    }
}
