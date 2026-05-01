package io.ananta.eclipse.runtime.project;

import java.util.List;
import java.util.Objects;

public final class EvolveProjectWizard {
    public EvolutionGoalRequest buildRequest(String projectName, String goal, List<String> selectedPaths) {
        return new EvolutionGoalRequest(
                Objects.toString(projectName, "").trim(),
                Objects.toString(goal, "").trim(),
                selectedPaths == null ? List.of() : List.copyOf(selectedPaths),
                true
        );
    }

    public record EvolutionGoalRequest(String projectName, String goal, List<String> selectedPaths, boolean hubGoalFirst) {
        public EvolutionGoalRequest {
            selectedPaths = selectedPaths == null ? List.of() : List.copyOf(selectedPaths);
        }
    }
}
