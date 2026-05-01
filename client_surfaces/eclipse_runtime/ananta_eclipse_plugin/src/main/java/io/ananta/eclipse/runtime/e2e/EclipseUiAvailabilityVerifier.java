package io.ananta.eclipse.runtime.e2e;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.eclipse.core.runtime.IConfigurationElement;
import org.eclipse.core.runtime.Platform;
import org.eclipse.equinox.app.IApplication;
import org.eclipse.equinox.app.IApplicationContext;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.IWorkbenchWindow;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.application.IWorkbenchWindowConfigurer;
import org.eclipse.ui.application.WorkbenchAdvisor;
import org.eclipse.ui.application.WorkbenchWindowAdvisor;
import org.osgi.framework.Bundle;

public final class EclipseUiAvailabilityVerifier implements IApplication {
    private static final String PLUGIN_ID = "io.ananta.eclipse.runtime";
    private static final String PERSPECTIVE_ID = "io.ananta.eclipse.perspective";
    private static final String[] REQUIRED_VIEW_IDS = {
        "io.ananta.eclipse.view.chat",
        "io.ananta.eclipse.view.status",
        "io.ananta.eclipse.view.goal",
        "io.ananta.eclipse.view.task_list",
        "io.ananta.eclipse.view.task_detail",
        "io.ananta.eclipse.view.artifact",
        "io.ananta.eclipse.view.approval_queue",
        "io.ananta.eclipse.view.audit",
        "io.ananta.eclipse.view.repair",
        "io.ananta.eclipse.view.tui_status",
        "io.ananta.eclipse.view.policy_fallback"
    };

    @Override
    public Object start(IApplicationContext context) throws Exception {
        String[] args = applicationArgs(context);
        Path reportPath = reportPath(args);
        AtomicReference<Map<String, Object>> workbenchReport = new AtomicReference<>(new LinkedHashMap<>());
        Display display = PlatformUI.createDisplay();
        int returnCode;
        try {
            returnCode = PlatformUI.createAndRunWorkbench(display, new VerificationWorkbenchAdvisor(workbenchReport));
        } finally {
            display.dispose();
        }

        Map<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "ananta_eclipse_ui_availability_report_v1");
        report.put("plugin_id", PLUGIN_ID);
        report.put("perspective_id", PERSPECTIVE_ID);
        report.put("bundle_state", bundleState());
        report.putAll(workbenchReport.get());
        report.put("workbench_return_code", returnCode);
        report.put("ok", Boolean.TRUE.equals(report.get("all_required_views_registered"))
            && Boolean.TRUE.equals(report.get("all_required_views_opened")));
        writeReport(reportPath, report);
        return Boolean.TRUE.equals(report.get("ok")) ? IApplication.EXIT_OK : Integer.valueOf(2);
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
        return Path.of("ananta-eclipse-ui-availability-report.json");
    }

    private static String bundleState() {
        Bundle bundle = Platform.getBundle(PLUGIN_ID);
        if (bundle == null) {
            return "missing";
        }
        return switch (bundle.getState()) {
            case Bundle.UNINSTALLED -> "uninstalled";
            case Bundle.INSTALLED -> "installed";
            case Bundle.RESOLVED -> "resolved";
            case Bundle.STARTING -> "starting";
            case Bundle.STOPPING -> "stopping";
            case Bundle.ACTIVE -> "active";
            default -> "state_" + bundle.getState();
        };
    }

    private static Map<String, Boolean> registeredViews() {
        Map<String, Boolean> registered = new LinkedHashMap<>();
        for (String viewId : REQUIRED_VIEW_IDS) {
            registered.put(viewId, Boolean.FALSE);
        }
        IConfigurationElement[] elements = Platform.getExtensionRegistry().getConfigurationElementsFor("org.eclipse.ui.views");
        for (IConfigurationElement element : elements) {
            String id = element.getAttribute("id");
            if (registered.containsKey(id)) {
                registered.put(id, Boolean.TRUE);
            }
        }
        return registered;
    }

    private static final class VerificationWorkbenchAdvisor extends WorkbenchAdvisor {
        private final AtomicReference<Map<String, Object>> report;

        private VerificationWorkbenchAdvisor(AtomicReference<Map<String, Object>> report) {
            this.report = report;
        }

        @Override
        public String getInitialWindowPerspectiveId() {
            return PERSPECTIVE_ID;
        }

        @Override
        public WorkbenchWindowAdvisor createWorkbenchWindowAdvisor(IWorkbenchWindowConfigurer configurer) {
            return new WorkbenchWindowAdvisor(configurer) {
                @Override
                public void postWindowOpen() {
                    Map<String, Object> nextReport = new LinkedHashMap<>();
                    Map<String, Boolean> registered = registeredViews();
                    Map<String, Boolean> opened = new LinkedHashMap<>();
                    List<String> openErrors = new ArrayList<>();
                    IWorkbenchWindow window = getWindowConfigurer().getWindow();
                    IWorkbenchPage page = window.getActivePage();
                    for (String viewId : REQUIRED_VIEW_IDS) {
                        try {
                            page.showView(viewId);
                            opened.put(viewId, Boolean.TRUE);
                        } catch (Exception exception) {
                            opened.put(viewId, Boolean.FALSE);
                            openErrors.add(viewId + ": " + exception.getClass().getSimpleName() + ": " + exception.getMessage());
                        }
                    }
                    nextReport.put("registered_views", registered);
                    nextReport.put("opened_views", opened);
                    nextReport.put("open_errors", openErrors);
                    nextReport.put("all_required_views_registered", registered.values().stream().allMatch(Boolean.TRUE::equals));
                    nextReport.put("all_required_views_opened", opened.values().stream().allMatch(Boolean.TRUE::equals));
                    report.set(nextReport);
                    PlatformUI.getWorkbench().close();
                }
            };
        }
    }

    private static void writeReport(Path path, Map<String, Object> report) throws IOException {
        Files.createDirectories(path.toAbsolutePath().getParent());
        Files.writeString(path, toJson(report) + "\n", StandardCharsets.UTF_8);
    }

    private static String toJson(Object value) {
        if (value instanceof Map<?, ?> map) {
            List<String> entries = new ArrayList<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                entries.add(toJson(String.valueOf(entry.getKey())) + ": " + toJson(entry.getValue()));
            }
            return "{" + String.join(", ", entries) + "}";
        }
        if (value instanceof Iterable<?> iterable) {
            List<String> entries = new ArrayList<>();
            for (Object item : iterable) {
                entries.add(toJson(item));
            }
            return "[" + String.join(", ", entries) + "]";
        }
        if (value instanceof Boolean || value instanceof Number) {
            return String.valueOf(value);
        }
        return "\"" + String.valueOf(value).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }
}
