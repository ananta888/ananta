package io.ananta.eclipse.runtime.e2e;

import org.eclipse.ui.IStartup;

public final class EclipseUiStartupVerifier implements IStartup {
    @Override
    public void earlyStartup() {
        EclipseUiVerificationSupport.runFromWorkbenchStartup(System.getProperty("ananta.e2e.report"));
    }
}
