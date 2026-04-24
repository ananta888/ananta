package io.ananta.eclipse.runtime.views;

import java.io.IOException;
import java.util.Map;
import java.util.Objects;

public final class EclipseTuiRuntimeBridge {
    public static final String DEFAULT_TUI_LAUNCH_COMMAND = "python -m client_surfaces.tui_runtime.ananta_tui --fixture";

    public TuiStatusPanel buildStatusPanel(
            String profileId,
            String endpoint,
            boolean runtimeAvailable,
            Map<String, String> handoffContext
    ) {
        return new TuiStatusPanel(
                runtimeAvailable,
                DEFAULT_TUI_LAUNCH_COMMAND,
                Objects.toString(profileId, "").trim(),
                Objects.toString(endpoint, "").trim(),
                handoffContext == null ? Map.of() : Map.copyOf(handoffContext)
        );
    }

    public TuiLaunchResult launch(
            String launchCommand,
            Map<String, String> handoffContext,
            boolean runtimeAvailable,
            boolean startProcess
    ) {
        if (!runtimeAvailable) {
            return TuiLaunchResult.failed("tui_runtime_missing", launchCommand);
        }
        String normalizedCommand = Objects.toString(launchCommand, "").trim();
        if (normalizedCommand.isBlank()) {
            return TuiLaunchResult.failed("launch_command_missing", launchCommand);
        }
        if (!startProcess) {
            return TuiLaunchResult.pending(normalizedCommand);
        }
        ProcessBuilder processBuilder = new ProcessBuilder("sh", "-lc", normalizedCommand);
        Map<String, String> environment = processBuilder.environment();
        if (handoffContext != null) {
            for (Map.Entry<String, String> entry : handoffContext.entrySet()) {
                String key = Objects.toString(entry.getKey(), "").trim();
                if (key.isBlank()) {
                    continue;
                }
                String value = Objects.toString(entry.getValue(), "").trim();
                environment.put("ANANTA_TUI_" + key.toUpperCase(), value);
            }
        }
        try {
            Process process = processBuilder.start();
            return TuiLaunchResult.started(normalizedCommand, process.pid());
        } catch (IOException exc) {
            return TuiLaunchResult.failed("launch_failed:" + Objects.toString(exc.getMessage(), "io_error"), normalizedCommand);
        }
    }

    public record TuiStatusPanel(
            boolean runtimeAvailable,
            String launchCommand,
            String profileId,
            String endpoint,
            Map<String, String> handoffContext
    ) {
        public TuiStatusPanel {
            handoffContext = handoffContext == null ? Map.of() : Map.copyOf(handoffContext);
        }
    }

    public record TuiLaunchResult(
            boolean started,
            boolean pendingStart,
            String failureReason,
            String launchCommand,
            Long pid
    ) {
        public static TuiLaunchResult pending(String launchCommand) {
            return new TuiLaunchResult(false, true, null, launchCommand, null);
        }

        public static TuiLaunchResult started(String launchCommand, long pid) {
            return new TuiLaunchResult(true, false, null, launchCommand, pid);
        }

        public static TuiLaunchResult failed(String failureReason, String launchCommand) {
            return new TuiLaunchResult(false, false, failureReason, launchCommand, null);
        }
    }
}
