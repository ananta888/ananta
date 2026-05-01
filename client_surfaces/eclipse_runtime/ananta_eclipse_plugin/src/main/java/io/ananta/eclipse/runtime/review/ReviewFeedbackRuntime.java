package io.ananta.eclipse.runtime.review;

import java.util.List;
import java.util.Objects;

public final class ReviewFeedbackRuntime {
    public ReviewFeedback build(String taskId, List<HunkComment> comments) {
        return new ReviewFeedback(
                Objects.toString(taskId, "").trim(),
                comments == null ? List.of() : List.copyOf(comments),
                true
        );
    }

    public record HunkComment(String path, int line, String comment) {
        public HunkComment {
            path = Objects.toString(path, "").trim();
            comment = Objects.toString(comment, "").trim();
        }
    }

    public record ReviewFeedback(String taskId, List<HunkComment> comments, boolean sentToHubReviewFlow) {
        public ReviewFeedback {
            comments = comments == null ? List.of() : List.copyOf(comments);
        }
    }
}
