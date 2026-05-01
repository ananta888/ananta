package io.ananta.eclipse.runtime.commands.handlers;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.commands.RuntimeCommandExecutionResult;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;

import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.ui.PartInitException;
import org.eclipse.ui.handlers.HandlerUtil;

import java.util.List;

final class EclipseHandlerUiSupport {
    private EclipseHandlerUiSupport() {
    }

    static Object executeAndOpenView(
            ExecutionEvent event,
            String commandId,
            String operationPreset,
            String targetViewId
    ) throws ExecutionException {
        RuntimeCommandExecutionResult result = AnantaRuntimeBootstrap.commandRegistry().execute(
                new EclipseCommandRegistry.CommandInvocation(
                        commandId,
                        "",
                        operationPreset,
                        AnantaRuntimeBootstrap.profile().getProfileId(),
                        null,
                        null,
                        new EclipseContextCaptureRuntime.WorkspaceState(null, null, null, List.of()),
                        new EclipseContextCaptureRuntime.EditorState(null, null, null)
                )
        );
        String message = result.isAllowed()
                ? "Command submitted. state=" + (result.getResponse() == null ? "unknown" : result.getResponse().getState().name().toLowerCase())
                : "Command blocked: " + result.getDenialReason();
        MessageDialog.openInformation(HandlerUtil.getActiveShell(event), "Ananta", message);
        try {
            HandlerUtil.getActiveWorkbenchWindowChecked(event).getActivePage().showView(targetViewId);
        } catch (PartInitException exc) {
            throw new ExecutionException("failed_to_open_view:" + targetViewId, exc);
        }
        return null;
    }
}
