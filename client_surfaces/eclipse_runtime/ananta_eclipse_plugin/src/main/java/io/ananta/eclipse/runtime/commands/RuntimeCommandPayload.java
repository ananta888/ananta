package io.ananta.eclipse.runtime.commands;

public record RuntimeCommandPayload(
        String goalText,
        String boundedContextJson,
        String operationPreset,
        String profileId,
        String blueprintId,
        String workProfileId
) {
}
