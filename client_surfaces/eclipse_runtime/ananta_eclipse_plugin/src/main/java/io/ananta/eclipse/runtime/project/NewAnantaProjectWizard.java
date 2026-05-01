package io.ananta.eclipse.runtime.project;

import java.util.List;
import java.util.Objects;

public final class NewAnantaProjectWizard {
    public ProjectGoalRequest buildRequest(
            String targetDirectory,
            String language,
            String architectureGoal,
            List<String> constraints,
            String workProfile
    ) {
        return new ProjectGoalRequest(
                Objects.toString(targetDirectory, "").trim(),
                Objects.toString(language, "").trim(),
                Objects.toString(architectureGoal, "").trim(),
                constraints == null ? List.of() : List.copyOf(constraints),
                Objects.toString(workProfile, "").trim(),
                true
        );
    }

    public record ProjectGoalRequest(
            String targetDirectory,
            String language,
            String architectureGoal,
            List<String> constraints,
            String workProfile,
            boolean createsHubGoalFirst
    ) {
        public ProjectGoalRequest {
            constraints = constraints == null ? List.of() : List.copyOf(constraints);
        }
    }
}
