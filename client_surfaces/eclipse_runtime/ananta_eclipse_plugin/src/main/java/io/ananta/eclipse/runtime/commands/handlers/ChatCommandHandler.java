package io.ananta.eclipse.runtime.commands.handlers;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;
import org.eclipse.ui.PartInitException;
import org.eclipse.ui.handlers.HandlerUtil;

public final class ChatCommandHandler extends AbstractHandler {
    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        try {
            HandlerUtil.getActiveWorkbenchWindowChecked(event)
                    .getActivePage()
                    .showView("io.ananta.eclipse.view.chat");
        } catch (PartInitException exc) {
            throw new ExecutionException("failed_to_open_chat_view", exc);
        }
        return null;
    }
}
