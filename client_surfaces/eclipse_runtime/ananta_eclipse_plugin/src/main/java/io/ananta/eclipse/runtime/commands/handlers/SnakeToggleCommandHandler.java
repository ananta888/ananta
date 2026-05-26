package io.ananta.eclipse.runtime.commands.handlers;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.snake.AnantaSnakeState;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.ui.PartInitException;
import org.eclipse.ui.handlers.HandlerUtil;

public final class SnakeToggleCommandHandler extends AbstractHandler {
    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        AnantaSnakeState state = AnantaRuntimeBootstrap.snakeService().toggleEnabled();
        String message = state.isEnabled() ? "Snake overlay enabled" : "Snake overlay disabled";
        MessageDialog.openInformation(HandlerUtil.getActiveShell(event), "Ananta Snake", message);
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
