package io.ananta.eclipse.runtime.snake;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.core.DegradedState;

import java.util.List;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public final class AnantaSnakePluginService {
    private static final int MIN_ACTIVE_TICK_RATE_FPS = 15;
    private static final int MAX_ACTIVE_TICK_RATE_FPS = 30;
    private static final int INACTIVE_TICK_RATE_FPS = 5;
    private static final int DEFAULT_FOLLOW_DISTANCE_PX = 24;
    private static final long HEARTBEAT_PERIOD_SECONDS = 20L;
    private static final long CONTEXT_DEBOUNCE_MILLIS = 300L;

    private static final Set<String> FOLLOW_MODES = Set.of("follow_mouse", "lurking", "paused");
    private static final Set<String> CONTEXT_MODES = Set.of(
            "idle",
            "observing",
            "editor_focus",
            "problem_focus",
            "console_focus",
            "diff_focus"
    );
    private static final Set<String> HUB_STATES = Set.of("offline", "local_only", "hub_connected");

    private final Object lock = new Object();
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "ananta-snake-runtime");
        thread.setDaemon(true);
        return thread;
    });
    private final AnantaIdeZoneRuntime zoneRuntime = new AnantaIdeZoneRuntime();
    private final AnantaSnakePredictionRuntime predictionRuntime = new AnantaSnakePredictionRuntime();
    private final AnantaEditorContextSnapshotRuntime contextSnapshotRuntime = new AnantaEditorContextSnapshotRuntime();
    private final AnantaSnakeContextEnvelopeRuntime contextEnvelopeRuntime = new AnantaSnakeContextEnvelopeRuntime();

    private AnantaSnakeState state = AnantaSnakeState.initial();
    private ScheduledFuture<?> tickFuture;
    private ScheduledFuture<?> heartbeatFuture;
    private ScheduledFuture<?> debouncedDispatchFuture;
    private int configuredActiveTickRateFps = 20;
    private int stillTicks;
    private boolean hasPreviousMousePosition;
    private int previousMouseX;
    private int previousMouseY;
    private AnantaIdeContextEvent latestIdeContextEvent = new AnantaIdeContextEvent("unknown", "", "", 0L);
    private AnantaSnakePredictionEvent latestPredictionEvent = AnantaSnakePredictionEvent.unknown(System.currentTimeMillis());
    private AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot latestEditorSnapshot = contextSnapshotRuntime.captureSnapshot(
            "",
            "",
            "unknown",
            new AnantaEditorContextSnapshotRuntime.SelectionRange(0, 0)
    );
    private AnantaSnakeHubConnectionConfig hubConnectionConfig = AnantaSnakeHubConnectionConfig.disabled();
    private AnantaApiClient hubApiClient;
    private String registeredClientId = "";
    private String workspaceId = "default-workspace";
    private String displayName = "ananta-eclipse-snake";
    private String pendingContextEnvelopeJson = "";
    private String lastContextEnvelopeJson = "";
    private String lastAskResult = "not_requested";

    public AnantaSnakeState snapshot() {
        synchronized (lock) {
            return state;
        }
    }

    public AnantaSnakeHubConnectionConfig hubConnectionConfig() {
        synchronized (lock) {
            return hubConnectionConfig;
        }
    }

    public AnantaSnakePredictionEvent latestPredictionEvent() {
        synchronized (lock) {
            return latestPredictionEvent;
        }
    }

    public String lastContextEnvelopeJson() {
        synchronized (lock) {
            return lastContextEnvelopeJson;
        }
    }

    public String lastAskResult() {
        synchronized (lock) {
            return lastAskResult;
        }
    }

    public AnantaSnakeState start() {
        synchronized (lock) {
            if (state.isRunning()) {
                return state;
            }
            state = state
                    .withEnabled(true)
                    .withRunning(true)
                    .withFollowDistance(DEFAULT_FOLLOW_DISTANCE_PX)
                    .withModes("follow_mouse", "observing")
                    .withWorkbenchActive(true)
                    .withTickRate(configuredActiveTickRateFps);
            scheduleTicksLocked();
            refreshPredictionLocked();
            return state;
        }
    }

    public AnantaSnakeState stop() {
        synchronized (lock) {
            cancelTicksLocked();
            stillTicks = 0;
            hasPreviousMousePosition = false;
            state = state.withRunning(false).withModes(state.getFollowMode(), "idle");
            refreshPredictionLocked();
            return state;
        }
    }

    public AnantaSnakeState restart() {
        synchronized (lock) {
            stop();
            return start();
        }
    }

    public AnantaSnakeState toggleEnabled() {
        synchronized (lock) {
            if (state.isEnabled()) {
                stop();
                state = state.withEnabled(false);
            } else {
                state = state.withEnabled(true);
                start();
            }
            return state;
        }
    }

    public AnantaSnakeState setFollowMode(String followMode) {
        String normalized = normalize(followMode);
        if (!FOLLOW_MODES.contains(normalized)) {
            throw new IllegalArgumentException("snake_follow_mode_invalid");
        }
        synchronized (lock) {
            state = state.withModes(normalized, state.getContextMode());
            refreshPredictionLocked();
            return state;
        }
    }

    public AnantaSnakeState setFollowDistancePx(int followDistancePx) {
        synchronized (lock) {
            state = state.withFollowDistance(Math.max(4, followDistancePx));
            return state;
        }
    }

    public AnantaSnakeState setContextMode(String contextMode) {
        String normalized = normalize(contextMode);
        if (!CONTEXT_MODES.contains(normalized)) {
            throw new IllegalArgumentException("snake_context_mode_invalid");
        }
        synchronized (lock) {
            state = state.withModes(state.getFollowMode(), normalized);
            refreshPredictionLocked();
            return state;
        }
    }

    public AnantaSnakeState setHubConnectionState(String hubConnectionState) {
        String normalized = normalize(hubConnectionState);
        if (!HUB_STATES.contains(normalized)) {
            throw new IllegalArgumentException("snake_hub_state_invalid");
        }
        synchronized (lock) {
            state = state.withHubConnectionState(normalized);
            return state;
        }
    }

    public AnantaSnakeState setTickRateFps(int activeTickRateFps) {
        int clamped = Math.max(MIN_ACTIVE_TICK_RATE_FPS, Math.min(MAX_ACTIVE_TICK_RATE_FPS, activeTickRateFps));
        synchronized (lock) {
            configuredActiveTickRateFps = clamped;
            int effective = state.isWorkbenchActive() ? configuredActiveTickRateFps : INACTIVE_TICK_RATE_FPS;
            state = state.withTickRate(effective);
            if (state.isRunning()) {
                scheduleTicksLocked();
            }
            return state;
        }
    }

    public AnantaSnakeState setWorkbenchActive(boolean workbenchActive) {
        synchronized (lock) {
            state = state.withWorkbenchActive(workbenchActive);
            int effective = workbenchActive ? configuredActiveTickRateFps : INACTIVE_TICK_RATE_FPS;
            state = state.withTickRate(effective);
            if (state.isRunning()) {
                scheduleTicksLocked();
            }
            return state;
        }
    }

    public AnantaIdeContextEvent latestIdeContextEvent() {
        synchronized (lock) {
            return latestIdeContextEvent;
        }
    }

    public AnantaSnakeState recordActiveWorkbenchPart(String partId, String partTitle) {
        AnantaIdeContextEvent event = zoneRuntime.buildEvent(partId, partTitle);
        synchronized (lock) {
            latestIdeContextEvent = event;
            state = state.withIdeZone(event.zone());
            if (!state.isRunning()) {
                state = state.withModes(state.getFollowMode(), contextModeForZone(event.zone()));
            }
            refreshPredictionLocked();
            return state;
        }
    }

    public AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot captureEditorContextSnapshot(
            String projectName,
            String filePath,
            String editorType,
            int selectionStart,
            int selectionEnd
    ) {
        AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot snapshot = contextSnapshotRuntime.captureSnapshot(
                projectName,
                filePath,
                editorType,
                new AnantaEditorContextSnapshotRuntime.SelectionRange(selectionStart, selectionEnd)
        );
        synchronized (lock) {
            latestEditorSnapshot = snapshot;
            refreshPredictionLocked();
            return latestEditorSnapshot;
        }
    }

    public AnantaSnakeState updateMousePosition(
            AnantaMouseTrackingRuntime.Point mousePoint,
            AnantaMouseTrackingRuntime.Bounds sourceBounds,
            AnantaMouseTrackingRuntime.Bounds overlayBounds
    ) {
        AnantaMouseTrackingRuntime runtime = new AnantaMouseTrackingRuntime();
        AnantaMouseTrackingRuntime.Point normalized = runtime.normalizePoint(mousePoint, sourceBounds, overlayBounds);
        synchronized (lock) {
            boolean samePosition = hasPreviousMousePosition
                    && mousePoint.x() == previousMouseX
                    && mousePoint.y() == previousMouseY;
            stillTicks = samePosition ? stillTicks + 1 : 0;
            previousMouseX = mousePoint.x();
            previousMouseY = mousePoint.y();
            hasPreviousMousePosition = true;
            state = state.withMouseAndOverlay(mousePoint.x(), mousePoint.y(), state.getOverlayX(), state.getOverlayY());
            if (!state.isRunning()) {
                state = state.withMouseAndOverlay(mousePoint.x(), mousePoint.y(), normalized.x(), normalized.y());
            }
            if (stillTicks >= 3) {
                state = state.withModes("lurking", contextModeForZone(state.getIdeZone()));
            } else {
                state = state.withModes("follow_mouse", "observing");
            }
            refreshPredictionLocked();
            queueContextEnvelopeDispatchLocked("policy_default_deny", List.of("file_content"), List.of());
            return state;
        }
    }

    public AnantaSnakeState tickNowForTest() {
        synchronized (lock) {
            tickLocked();
            return state;
        }
    }

    public AnantaSnakeState applyHubProfile(ClientProfile profile, boolean enabled) {
        AnantaSnakeHubConnectionConfig config = AnantaSnakeHubConnectionConfig.fromProfile(profile, enabled);
        return configureHubConnection(config);
    }

    public AnantaSnakeState configureHubConnection(AnantaSnakeHubConnectionConfig config) {
        AnantaSnakeHubConnectionConfig input = config == null ? AnantaSnakeHubConnectionConfig.disabled() : config;
        synchronized (lock) {
            hubConnectionConfig = input;
            if (!hubConnectionConfig.enabled()) {
                hubApiClient = null;
                registeredClientId = "";
                cancelHeartbeatLocked();
                state = state.withHubConnectionState("local_only");
                return state;
            }
            hubApiClient = new AnantaApiClient(hubConnectionConfig.toClientProfile());
            state = state.withHubConnectionState("local_only");
            return state;
        }
    }

    public ClientResponse registerSnakeClient(String nextWorkspaceId, String nextDisplayName) {
        AnantaApiClient apiClient;
        String workspace;
        String name;
        synchronized (lock) {
            if (!hubConnectionConfig.enabled() || hubApiClient == null) {
                state = state.withHubConnectionState("local_only");
                return localOnlyResponse("snake_registration_skipped");
            }
            workspaceId = sanitizeFallback(nextWorkspaceId, workspaceId);
            displayName = sanitizeFallback(nextDisplayName, displayName);
            workspace = workspaceId;
            name = displayName;
            apiClient = hubApiClient;
        }

        ClientResponse response = apiClient.registerSnakeClient(
                workspace,
                name,
                List.of("snake_overlay", "ide_context_metadata", "prediction_events", "ask_action")
        );

        synchronized (lock) {
            if (response.isOk()) {
                registeredClientId = "snake-client-" + workspace;
                state = state.withHubConnectionState("hub_connected");
                scheduleHeartbeatLocked();
            } else {
                state = state.withHubConnectionState("local_only");
            }
            return response;
        }
    }

    public ClientResponse heartbeatNowForTest() {
        synchronized (lock) {
            return sendHeartbeatLocked();
        }
    }

    public void queueContextEnvelopeDispatch(
            String policyDecisionRef,
            List<String> deniedContextRefs,
            List<String> artifactRefs
    ) {
        synchronized (lock) {
            queueContextEnvelopeDispatchLocked(policyDecisionRef, deniedContextRefs, artifactRefs);
        }
    }

    public ClientResponse dispatchContextEnvelopeNowForTest(
            String policyDecisionRef,
            List<String> deniedContextRefs,
            List<String> artifactRefs
    ) {
        String envelopeJson;
        synchronized (lock) {
            envelopeJson = buildContextEnvelopeLocked(policyDecisionRef, deniedContextRefs, artifactRefs);
        }
        return dispatchEnvelopeToWorker(envelopeJson);
    }

    public ClientResponse askAnantaSnakeNow(String goalText) {
        String envelopeJson;
        AnantaApiClient apiClient;
        String normalizedGoal = sanitizeFallback(goalText, "Ask Ananta Snake");
        synchronized (lock) {
            envelopeJson = buildContextEnvelopeLocked("policy_default_deny", List.of("file_content"), List.of());
            if (!hubConnectionConfig.enabled() || hubApiClient == null) {
                state = state.withHubConnectionState("local_only");
                lastAskResult = "local_only: ask action skipped";
                return localOnlyResponse("snake_ask_skipped");
            }
            apiClient = hubApiClient;
        }

        ClientResponse response = apiClient.submitGoal(
                normalizedGoal,
                envelopeJson,
                "repository_understanding",
                "io.ananta.eclipse.command.snake_ask",
                null
        );
        synchronized (lock) {
            state = response.isOk() ? state.withHubConnectionState("hub_connected") : state.withHubConnectionState("local_only");
            lastAskResult = "ask_state=" + response.getState().name().toLowerCase()
                    + ", status=" + response.getStatusCode();
            return response;
        }
    }

    public void requestAskAnantaSnake(String goalText) {
        scheduler.execute(() -> askAnantaSnakeNow(goalText));
    }

    public void shutdown() {
        synchronized (lock) {
            cancelTicksLocked();
            cancelHeartbeatLocked();
            if (debouncedDispatchFuture != null) {
                debouncedDispatchFuture.cancel(false);
                debouncedDispatchFuture = null;
            }
            scheduler.shutdownNow();
        }
    }

    private void queueContextEnvelopeDispatchLocked(
            String policyDecisionRef,
            List<String> deniedContextRefs,
            List<String> artifactRefs
    ) {
        pendingContextEnvelopeJson = buildContextEnvelopeLocked(policyDecisionRef, deniedContextRefs, artifactRefs);
        if (debouncedDispatchFuture != null) {
            debouncedDispatchFuture.cancel(false);
        }
        debouncedDispatchFuture = scheduler.schedule(
                () -> dispatchEnvelopeToWorker(pendingContextEnvelopeJson),
                CONTEXT_DEBOUNCE_MILLIS,
                TimeUnit.MILLISECONDS
        );
    }

    private String buildContextEnvelopeLocked(
            String policyDecisionRef,
            List<String> deniedContextRefs,
            List<String> artifactRefs
    ) {
        AnantaSnakeContextEnvelopeRuntime.SnakeContextEnvelope envelope = contextEnvelopeRuntime.build(
                state.getIdeZone(),
                latestEditorSnapshot,
                latestPredictionEvent,
                sanitizeFallback(policyDecisionRef, "policy_default_deny"),
                deniedContextRefs,
                artifactRefs
        );
        lastContextEnvelopeJson = envelope.toJson();
        return lastContextEnvelopeJson;
    }

    private ClientResponse dispatchEnvelopeToWorker(String envelopeJson) {
        AnantaApiClient apiClient;
        synchronized (lock) {
            if (!hubConnectionConfig.enabled() || hubApiClient == null) {
                state = state.withHubConnectionState("local_only");
                return localOnlyResponse("context_dispatch_skipped");
            }
            apiClient = hubApiClient;
        }

        ClientResponse response = apiClient.submitGoal(
                "Process Eclipse Snake context envelope",
                envelopeJson,
                "repository_understanding",
                "io.ananta.eclipse.command.snake_context",
                null
        );
        synchronized (lock) {
            state = response.isOk() ? state.withHubConnectionState("hub_connected") : state.withHubConnectionState("local_only");
            return response;
        }
    }

    private ClientResponse sendHeartbeatLocked() {
        if (!hubConnectionConfig.enabled() || hubApiClient == null || registeredClientId.isBlank()) {
            state = state.withHubConnectionState("local_only");
            return localOnlyResponse("snake_heartbeat_skipped");
        }
        ClientResponse response = hubApiClient.snakeHeartbeat(registeredClientId, workspaceId);
        state = response.isOk() ? state.withHubConnectionState("hub_connected") : state.withHubConnectionState("local_only");
        return response;
    }

    private void scheduleHeartbeatLocked() {
        cancelHeartbeatLocked();
        heartbeatFuture = scheduler.scheduleAtFixedRate(
                () -> {
                    synchronized (lock) {
                        sendHeartbeatLocked();
                    }
                },
                HEARTBEAT_PERIOD_SECONDS,
                HEARTBEAT_PERIOD_SECONDS,
                TimeUnit.SECONDS
        );
    }

    private void cancelHeartbeatLocked() {
        if (heartbeatFuture != null) {
            heartbeatFuture.cancel(false);
            heartbeatFuture = null;
        }
    }

    private void scheduleTicksLocked() {
        cancelTicksLocked();
        long periodMillis = Math.max(20L, 1000L / Math.max(1, state.getTickRateFps()));
        tickFuture = scheduler.scheduleAtFixedRate(this::tick, periodMillis, periodMillis, TimeUnit.MILLISECONDS);
    }

    private void cancelTicksLocked() {
        if (tickFuture != null) {
            tickFuture.cancel(false);
            tickFuture = null;
        }
    }

    private void tick() {
        synchronized (lock) {
            tickLocked();
        }
    }

    private void tickLocked() {
        if (!state.isRunning() || "paused".equals(state.getFollowMode())) {
            return;
        }
        int snakeX = state.getOverlayX();
        int snakeY = state.getOverlayY();
        int mouseX = state.getMouseX();
        int mouseY = state.getMouseY();
        int toMouseX = mouseX - snakeX;
        int toMouseY = mouseY - snakeY;
        double distance = Math.sqrt((toMouseX * toMouseX) + (toMouseY * toMouseY));
        if (distance <= state.getFollowDistancePx() + 2) {
            state = state.withModes("lurking", contextModeForZone(state.getIdeZone()));
            refreshPredictionLocked();
            return;
        }
        double safeDistance = Math.max(1.0, distance);
        double normalizedX = toMouseX / safeDistance;
        double normalizedY = toMouseY / safeDistance;
        int targetX = (int) Math.round(mouseX - (normalizedX * state.getFollowDistancePx()));
        int targetY = (int) Math.round(mouseY - (normalizedY * state.getFollowDistancePx()));
        int deltaX = targetX - snakeX;
        int deltaY = targetY - snakeY;
        int maxStep = Math.max(2, Math.min(12, (int) Math.round(distance / 4.0)));
        int stepX = clampMagnitude(deltaX, maxStep);
        int stepY = clampMagnitude(deltaY, maxStep);
        int nextX = snakeX + stepX;
        int nextY = snakeY + stepY;
        state = state.withMouseAndOverlay(mouseX, mouseY, nextX, nextY).withModes("follow_mouse", "observing");
        refreshPredictionLocked();
    }

    private void refreshPredictionLocked() {
        latestPredictionEvent = predictionRuntime.predict(
                state.getIdeZone(),
                state.getFollowMode(),
                state.getContextMode(),
                System.currentTimeMillis()
        );
    }

    private static ClientResponse localOnlyResponse(String reason) {
        return new ClientResponse(
                true,
                null,
                DegradedState.HEALTHY,
                "{\"mode\":\"local_only\"}",
                reason,
                false
        );
    }

    private static String sanitizeFallback(String value, String fallback) {
        String normalized = value == null ? "" : value.trim();
        return normalized.isBlank() ? fallback : normalized;
    }

    private static int clampMagnitude(int value, int maxMagnitude) {
        if (value == 0) {
            return 0;
        }
        int magnitude = Math.min(Math.abs(value), maxMagnitude);
        return Integer.compare(value, 0) * magnitude;
    }

    private static String contextModeForZone(String ideZone) {
        return switch (normalize(ideZone)) {
            case "editor" -> "editor_focus";
            case "problems" -> "problem_focus";
            case "console" -> "console_focus";
            case "git_compare" -> "diff_focus";
            default -> "observing";
        };
    }

    private static String normalize(String value) {
        return value == null ? "" : value.trim().toLowerCase();
    }
}
