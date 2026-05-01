package io.ananta.eclipse.runtime.commands.handlers;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.commands.RuntimeCommandExecutionResult;
import io.ananta.eclipse.runtime.commands.RuntimeCommandType;
import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;

import java.util.Objects;

public final class ReviewCommandHandler extends AbstractHandler {
    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        return null;
    }

    public RuntimeCommandExecutionResult execute(
            EclipseCommandRegistry registry,
            EclipseCommandRegistry.CommandInvocation invocation
    ) {
        Objects.requireNonNull(registry, "registry");
        Objects.requireNonNull(invocation, "invocation");
        return registry.execute(
                new EclipseCommandRegistry.CommandInvocation(
                        RuntimeCommandType.REVIEW.commandId(),
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
