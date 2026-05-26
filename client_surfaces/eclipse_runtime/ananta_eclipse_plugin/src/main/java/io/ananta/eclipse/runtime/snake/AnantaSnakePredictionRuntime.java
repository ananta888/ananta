package io.ananta.eclipse.runtime.snake;

import java.util.List;

public final class AnantaSnakePredictionRuntime {
    public AnantaSnakePredictionEvent predict(
            String ideZone,
            String followMode,
            String contextMode,
            long nowEpochMillis
    ) {
        String zone = normalize(ideZone);
        String mode = normalize(followMode);
        String context = normalize(contextMode);
        if ("problems".equals(zone)) {
            return event("wants_explain_error", mode, nowEpochMillis, List.of("zone=problems", "context=" + context));
        }
        if ("git_compare".equals(zone)) {
            return event("wants_review_diff", mode, nowEpochMillis, List.of("zone=git_compare", "context=" + context));
        }
        if ("editor".equals(zone)) {
            return event("wants_explain_file", mode, nowEpochMillis, List.of("zone=editor", "context=" + context));
        }
        if ("console".equals(zone)) {
            return event("wants_run_tests", mode, nowEpochMillis, List.of("zone=console", "context=" + context));
        }
        return AnantaSnakePredictionEvent.unknown(nowEpochMillis);
    }

    private static AnantaSnakePredictionEvent event(
            String intentKind,
            String followMode,
            long nowEpochMillis,
            List<String> evidence
    ) {
        boolean stable = "lurking".equals(followMode);
        double confidence = stable ? 0.74 : 0.41;
        long ttlMillis = stable ? 18_000L : 8_000L;
        return new AnantaSnakePredictionEvent(intentKind, confidence, evidence, nowEpochMillis + ttlMillis);
    }

    private static String normalize(String value) {
        return value == null ? "" : value.trim().toLowerCase();
    }
}
