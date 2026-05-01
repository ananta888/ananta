package io.ananta.eclipse.runtime.commands.handlers;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;

public final class EvolveProjectCommandHandler extends AbstractHandler {
    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        return EclipseHandlerUiSupport.executeAndOpenView(
                event,
                "io.ananta.eclipse.command.evolve_project",
                "project_evolution",
                "io.ananta.eclipse.view.goal"
        );
    }
}
