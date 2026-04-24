package io.ananta.eclipse.runtime.commands;

import java.util.Arrays;
import java.util.Locale;
import java.util.Optional;

public enum RuntimeCommandType {
    ANALYZE("io.ananta.eclipse.command.analyze", "analyze", "goals"),
    REVIEW("io.ananta.eclipse.command.review", "review", "review"),
    PATCH("io.ananta.eclipse.command.patch", "patch", "patch"),
    NEW_PROJECT("io.ananta.eclipse.command.new_project", "new_project", "projects"),
    EVOLVE_PROJECT("io.ananta.eclipse.command.evolve_project", "evolve_project", "projects");

    private final String commandId;
    private final String operationPreset;
    private final String requiredCapability;

    RuntimeCommandType(String commandId, String operationPreset, String requiredCapability) {
        this.commandId = commandId;
        this.operationPreset = operationPreset;
        this.requiredCapability = requiredCapability;
    }

    public String commandId() {
        return commandId;
    }

    public String operationPreset() {
        return operationPreset;
    }

    public String requiredCapability() {
        return requiredCapability;
    }

    public static Optional<RuntimeCommandType> fromCommandId(String commandId) {
        String normalized = commandId == null ? "" : commandId.trim().toLowerCase(Locale.ROOT);
        return Arrays.stream(values())
                .filter(value -> value.commandId.equalsIgnoreCase(normalized))
                .findFirst();
    }
}
