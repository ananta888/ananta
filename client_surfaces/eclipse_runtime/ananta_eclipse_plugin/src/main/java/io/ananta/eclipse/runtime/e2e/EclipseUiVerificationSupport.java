package io.ananta.eclipse.runtime.e2e;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.eclipse.core.runtime.IConfigurationElement;
import org.eclipse.core.runtime.Platform;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.IWorkbench;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.IWorkbenchWindow;
import org.eclipse.ui.PlatformUI;
import org.osgi.framework.Bundle;

final class EclipseUiVerificationSupport {
    static final String PLUGIN_ID = "io.ananta.eclipse.runtime";
    static final String PERSPECTIVE_ID = "io.ananta.eclipse.perspective";
    static final String[] REQUIRED_VIEW_IDS = {
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

    private EclipseUiVerificationSupport() {
    }

    static void runFromWorkbenchStartup(String reportPath) {
        if (reportPath == null || reportPath.isBlank()) {
            return;
        }
        Display.getDefault().asyncExec(() -> {
            Map<String, Object> report = verifyViews();
            try {
                writeReport(Path.of(reportPath), report);
            } catch (IOException exception) {
                exception.printStackTrace();
            } finally {
                PlatformUI.getWorkbench().close();
            }
        });
    }

    static Map<String, Object> verifyViews() {
        Map<String, Object> report = new LinkedHashMap<>();
        Map<String, Boolean> registered = registeredViews();
        Map<String, Boolean> opened = new LinkedHashMap<>();
        List<String> openErrors = new ArrayList<>();
        IWorkbench workbench = PlatformUI.getWorkbench();
        IWorkbenchWindow window = workbench.getActiveWorkbenchWindow();
        IWorkbenchPage page = window == null ? null : window.getActivePage();
        for (String viewId : REQUIRED_VIEW_IDS) {
            try {
                if (page == null) {
                    throw new IllegalStateException("active workbench page is unavailable");
                }
                page.showView(viewId);
                opened.put(viewId, Boolean.TRUE);
            } catch (Exception exception) {
                opened.put(viewId, Boolean.FALSE);
                openErrors.add(viewId + ": " + exception.getClass().getSimpleName() + ": " + exception.getMessage());
            }
        }
        report.put("schema", "ananta_eclipse_ui_availability_report_v1");
        report.put("plugin_id", PLUGIN_ID);
        report.put("perspective_id", PERSPECTIVE_ID);
        report.put("bundle_state", bundleState());
        report.put("registered_views", registered);
        report.put("opened_views", opened);
        report.put("open_errors", openErrors);
        report.put("all_required_views_registered", registered.values().stream().allMatch(Boolean.TRUE::equals));
        report.put("all_required_views_opened", opened.values().stream().allMatch(Boolean.TRUE::equals));
        report.put("ok", Boolean.TRUE.equals(report.get("all_required_views_registered"))
            && Boolean.TRUE.equals(report.get("all_required_views_opened")));
        return report;
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

    static void writeReport(Path path, Map<String, Object> report) throws IOException {
        Files.createDirectories(path.toAbsolutePath().getParent());
        Files.writeString(path, toJson(report) + "\n", StandardCharsets.UTF_8);
    }

    static String toJson(Object value) {
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
