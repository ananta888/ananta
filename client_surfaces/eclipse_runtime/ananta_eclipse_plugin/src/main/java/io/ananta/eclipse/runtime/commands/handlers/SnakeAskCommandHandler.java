package io.ananta.eclipse.runtime.commands.handlers;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;
import org.eclipse.ui.PartInitException;
import org.eclipse.ui.handlers.HandlerUtil;

public final class SnakeAskCommandHandler extends AbstractHandler {
    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        AnantaRuntimeBootstrap.snakeService().requestAskAnantaSnake("Explain current IDE context");
        try {
            HandlerUtil.getActiveWorkbenchWindowChecked(event)
                    .getActivePage()
                    .showView("io.ananta.eclipse.view.snake");
        } catch (PartInitException exc) {
            throw new ExecutionException("failed_to_open_snake_view", exc);
        }
        return null;
    }
}
