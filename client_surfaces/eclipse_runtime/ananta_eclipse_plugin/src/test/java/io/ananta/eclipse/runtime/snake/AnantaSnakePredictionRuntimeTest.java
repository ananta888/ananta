package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaSnakePredictionRuntimeTest {
    @Test
    void predictionMapsZonesToStableIntents() {
        AnantaSnakePredictionRuntime runtime = new AnantaSnakePredictionRuntime();
        long now = System.currentTimeMillis();
        assertEquals(
                "wants_explain_error",
                runtime.predict("problems", "lurking", "problem_focus", now).intentKind()
        );
        assertEquals(
                "wants_review_diff",
                runtime.predict("git_compare", "lurking", "diff_focus", now).intentKind()
        );
        assertEquals(
                "wants_explain_file",
                runtime.predict("editor", "lurking", "editor_focus", now).intentKind()
        );
        assertEquals(
                "wants_run_tests",
                runtime.predict("console", "lurking", "console_focus", now).intentKind()
        );
    }

    @Test
    void unknownPredictionStaysLowConfidence() {
        AnantaSnakePredictionRuntime runtime = new AnantaSnakePredictionRuntime();
        AnantaSnakePredictionEvent prediction = runtime.predict("unknown", "follow_mouse", "observing", System.currentTimeMillis());
        assertEquals("unknown", prediction.intentKind());
        assertTrue(prediction.confidence() <= 0.2);
    }
}
