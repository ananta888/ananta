package io.ananta.eclipse.runtime.commands.handlers;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.commands.RuntimeCommandExecutionResult;
import io.ananta.eclipse.runtime.commands.RuntimeCommandType;

import java.util.Objects;

public final class NewProjectCommandHandler {
    public RuntimeCommandExecutionResult execute(
            EclipseCommandRegistry registry,
            EclipseCommandRegistry.CommandInvocation invocation
    ) {
        Objects.requireNonNull(registry, "registry");
        Objects.requireNonNull(invocation, "invocation");
        return registry.execute(
                new EclipseCommandRegistry.CommandInvocation(
                        RuntimeCommandType.NEW_PROJECT.commandId(),
                        invocation.goalText(),
                        invocation.operationPreset(),
                        invocation.profileId(),
                        invocation.blueprintId(),
                        invocation.workProfileId(),
                        invocation.workspaceState(),
                        invocation.editorState()
                )
        );
    }
}
