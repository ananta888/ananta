package io.ananta.eclipse.runtime.e2e;

import java.nio.file.Path;
import java.util.Map;
import org.eclipse.equinox.app.IApplication;
import org.eclipse.equinox.app.IApplicationContext;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.application.WorkbenchAdvisor;

public final class EclipseUiAvailabilityVerifier implements IApplication {
    @Override
    public Object start(IApplicationContext context) throws Exception {
        Path reportPath = reportPath(applicationArgs(context));
        Display display = PlatformUI.createDisplay();
        try {
            PlatformUI.createAndRunWorkbench(display, new WorkbenchAdvisor() {
                @Override
                public String getInitialWindowPerspectiveId() {
                    return EclipseUiVerificationSupport.PERSPECTIVE_ID;
                }

                @Override
                public void postStartup() {
                    display.asyncExec(() -> {
                        Map<String, Object> report = EclipseUiVerificationSupport.verifyViews();
                        try {
                            EclipseUiVerificationSupport.writeReport(reportPath, report);
                        } catch (Exception exception) {
                            exception.printStackTrace();
                        } finally {
                            PlatformUI.getWorkbench().close();
                        }
                    });
                }
            });
        } finally {
            display.dispose();
        }
        return IApplication.EXIT_OK;
    }

    @Override
    public void stop() {
        if (PlatformUI.isWorkbenchRunning()) {
            PlatformUI.getWorkbench().close();
        }
    }

    private static String[] applicationArgs(IApplicationContext context) {
        Object args = context.getArguments().get(IApplicationContext.APPLICATION_ARGS);
        return args instanceof String[] values ? values : new String[0];
    }

    private static Path reportPath(String[] args) {
        for (int index = 0; index < args.length - 1; index++) {
            if ("-anantaVerifierReport".equals(args[index])) {
                return Path.of(args[index + 1]);
            }
        }
        String sysProp = System.getProperty("ananta.e2e.report");
        if (sysProp != null && !sysProp.isBlank()) {
            return Path.of(sysProp);
        }
        return Path.of("ananta-eclipse-ui-availability-report.json");
    }
}
