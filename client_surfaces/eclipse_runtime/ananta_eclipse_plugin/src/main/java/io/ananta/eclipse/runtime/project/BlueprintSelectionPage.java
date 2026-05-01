package io.ananta.eclipse.runtime.project;

import java.util.List;
import java.util.Objects;

public final class BlueprintSelectionPage {
    public BlueprintSelection select(String blueprintId, String workProfileId, List<String> availableBlueprints) {
        List<String> available = availableBlueprints == null ? List.of() : List.copyOf(availableBlueprints);
        String selected = Objects.toString(blueprintId, "").trim();
        boolean availableSelection = selected.isBlank() || available.contains(selected);
        return new BlueprintSelection(selected, Objects.toString(workProfileId, "").trim(), availableSelection, !availableSelection);
    }

    public record BlueprintSelection(
            String blueprintId,
            String workProfileId,
            boolean available,
            boolean degraded
    ) {
    }
}
