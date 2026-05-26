package io.ananta.eclipse.runtime.snake;

import java.util.Locale;
import java.util.Map;
import java.util.Objects;

public final class AnantaIdeZoneRuntime {
    private static final Map<String, String> KNOWN_PART_MAPPINGS = Map.ofEntries(
            Map.entry("org.eclipse.ui.editors", "editor"),
            Map.entry("org.eclipse.ui.views.problem", "problems"),
            Map.entry("org.eclipse.ui.console.consoleview", "console"),
            Map.entry("org.eclipse.jdt.ui.packages", "package_explorer"),
            Map.entry("org.eclipse.search.ui.views.searchview", "search"),
            Map.entry("org.eclipse.team.ui.synchronizeview", "git_compare"),
            Map.entry("org.eclipse.compare.compareeditor", "git_compare")
    );

    public String detectZone(String workbenchPartId) {
        String normalized = normalize(workbenchPartId);
        if (normalized.isBlank()) {
            return "unknown";
        }
        if (KNOWN_PART_MAPPINGS.containsKey(normalized)) {
            return KNOWN_PART_MAPPINGS.get(normalized);
        }
        if (normalized.contains("problem")) {
            return "problems";
        }
        if (normalized.contains("console")) {
            return "console";
        }
        if (normalized.contains("search")) {
            return "search";
        }
        if (normalized.contains("package") || normalized.contains("explorer")) {
            return "package_explorer";
        }
        if (normalized.contains("compare") || normalized.contains("diff") || normalized.contains("synchronize")) {
            return "git_compare";
        }
        if (normalized.contains("editor")) {
            return "editor";
        }
        return "unknown";
    }

    public AnantaIdeContextEvent buildEvent(String workbenchPartId, String partTitle) {
        String normalizedPartId = Objects.toString(workbenchPartId, "").trim();
        String normalizedTitle = Objects.toString(partTitle, "").trim();
        return new AnantaIdeContextEvent(
                detectZone(normalizedPartId),
                normalizedPartId,
                normalizedTitle,
                System.currentTimeMillis()
        );
    }

    private static String normalize(String value) {
        return Objects.toString(value, "").trim().toLowerCase(Locale.ROOT);
    }
}
