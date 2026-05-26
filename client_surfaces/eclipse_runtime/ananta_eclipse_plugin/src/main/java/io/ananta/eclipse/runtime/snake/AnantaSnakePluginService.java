package io.ananta.eclipse.runtime.snake;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.core.DegradedState;
import io.ananta.eclipse.runtime.security.TokenRedaction;

import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
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
    private static final Set<String> HUB_STATES = Set.of("offline", "local_only", "hub_connected", "ai_active");

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
    private AnantaSnakeUiPreferences uiPreferences = AnantaSnakeUiPreferences.defaults();
    private AnantaApiClient hubApiClient;
    private String registeredClientId = "";
    private String workspaceId = "default-workspace";
    private String displayName = "ananta-eclipse-snake";
    private String pendingContextEnvelopeJson = "";
    private String lastContextEnvelopeJson = "";
    private String lastContextSummary = "none";
    private String lastAskResult = "not_requested";
    private String lastPolicyReasonCode = "none";
    private boolean temporarilyHidden;
    private boolean presentationModeActive;

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

    public AnantaSnakeUiPreferences uiPreferences() {
        synchronized (lock) {
            return uiPreferences;
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

    public String lastContextSummary() {
        synchronized (lock) {
            return lastContextSummary;
        }
    }

    public String contextReleaseMode() {
        synchronized (lock) {
            if (uiPreferences.privacySettings().allowFileContent()) {
                return "file_content";
            }
            if (uiPreferences.privacySettings().allowSelectionContent()) {
                return "selection_content";
            }
            return "metadata_only";
        }
    }

    public int overlayOpacityPercent() {
        synchronized (lock) {
            return uiPreferences.overlayOpacityPercent();
        }
    }

    public String lastAskResult() {
        synchronized (lock) {
            return lastAskResult;
        }
    }

    public String lastPolicyReasonCode() {
        synchronized (lock) {
            return lastPolicyReasonCode;
        }
    }

    public boolean isTemporarilyHidden() {
        synchronized (lock) {
            return temporarilyHidden;
        }
    }

    public boolean isPresentationModeActive() {
        synchronized (lock) {
            return presentationModeActive;
        }
    }

    public boolean isDoNotDisturbActive() {
        synchronized (lock) {
            return uiPreferences.doNotDisturbMode();
        }
    }

    public void toggleTemporarilyHidden() {
        synchronized (lock) {
            temporarilyHidden = !temporarilyHidden;
        }
    }

    public void setPresentationMode(boolean active) {
        synchronized (lock) {
            presentationModeActive = active;
            if (presentationModeActive) {
                state = state.withTickRate(INACTIVE_TICK_RATE_FPS).withModes("paused", state.getContextMode());
            } else {
                state = state.withTickRate(configuredActiveTickRateFps).withModes("follow_mouse", state.getContextMode());
            }
        }
    }

    public void resetContextAuthorization() {
        synchronized (lock) {
            AnantaSnakePrivacySettings reset = AnantaSnakePrivacySettings.safeDefaults();
            uiPreferences = new AnantaSnakeUiPreferences(
                    uiPreferences.snakeEnabledByDefault(),
                    uiPreferences.animationFps(),
                    uiPreferences.followDistancePx(),
                    uiPreferences.overlayOpacityPercent(),
                    uiPreferences.localOnlyMode(),
                    uiPreferences.doNotDisturbMode(),
                    reset
            );
            lastPolicyReasonCode = "context_grants_reset";
        }
    }

    public AnantaSnakeState configureUiPreferences(AnantaSnakeUiPreferences preferences) {
        AnantaSnakeUiPreferences input = preferences == null ? AnantaSnakeUiPreferences.defaults() : preferences;
        synchronized (lock) {
            uiPreferences = input;
            configuredActiveTickRateFps = input.animationFps();
            state = state.withFollowDistance(input.followDistancePx());
            int effective = state.isWorkbenchActive() ? configuredActiveTickRateFps : INACTIVE_TICK_RATE_FPS;
            state = state.withTickRate(effective);
            if (state.isRunning()) {
                scheduleTicksLocked();
            }
            if (!input.snakeEnabledByDefault()) {
                stop();
                state = state.withEnabled(false);
            }
            if (input.doNotDisturbMode()) {
                state = state.withTickRate(INACTIVE_TICK_RATE_FPS);
            }
            return state;
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
                    .withFollowDistance(Math.max(DEFAULT_FOLLOW_DISTANCE_PX, uiPreferences.followDistancePx()))
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
            queueContextEnvelopeDispatchLocked("policy_default_deny", List.of(), List.of());
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
                state = state.withHubConnectionState(resolveHubFallbackState(response));
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
            if (uiPreferences.doNotDisturbMode() || presentationModeActive) {
                lastPolicyReasonCode = "do_not_disturb_active";
                return;
            }
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
            if (uiPreferences.doNotDisturbMode() || presentationModeActive) {
                lastAskResult = "do_not_disturb_active";
                return localOnlyResponse("do_not_disturb_active");
            }
            envelopeJson = buildContextEnvelopeLocked("policy_default_deny", List.of(), List.of());
            if (!hubConnectionConfig.enabled() || hubApiClient == null) {
                state = state.withHubConnectionState("local_only");
                lastAskResult = "local_only: ask action skipped";
                return localOnlyResponse("snake_ask_skipped");
            }
            if (!canUseCloudProvider()) {
                state = state.withHubConnectionState("local_only");
                lastPolicyReasonCode = "external_provider_denied";
                lastAskResult = "policy_denied: external_provider_denied";
                return policyDeniedResponse("external_provider_denied");
            }
            apiClient = hubApiClient;
        }

        SanitizedEnvelope sanitized = sanitizeEnvelope(envelopeJson);
        ClientResponse response = apiClient.submitGoal(
                normalizedGoal,
                sanitized.envelopeJson(),
                "repository_understanding",
                "io.ananta.eclipse.command.snake_ask",
                null
        );
        synchronized (lock) {
            if (response.isOk()) {
                state = state.withHubConnectionState("ai_active");
            } else {
                state = state.withHubConnectionState(resolveHubFallbackState(response));
            }
            if (!sanitized.reasonCodes().isEmpty()) {
                lastPolicyReasonCode = String.join(",", sanitized.reasonCodes());
            }
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
        if (uiPreferences.doNotDisturbMode() || presentationModeActive) {
            lastPolicyReasonCode = "do_not_disturb_active";
            return;
        }
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
        List<String> deniedRefs = new ArrayList<>(deniedContextRefs == null ? List.of() : deniedContextRefs);
        String resolvedPolicyDecisionRef = sanitizeFallback(policyDecisionRef, "policy_default_deny");
        if (!uiPreferences.privacySettings().allowSelectionContent()) {
            deniedRefs.add("selection_content");
        }
        if (!uiPreferences.privacySettings().allowFileContent()) {
            deniedRefs.add("file_content");
        }
        if (!canUseCloudProvider()) {
            deniedRefs.add("external_provider");
            resolvedPolicyDecisionRef = "policy_external_provider_blocked";
        }
        AnantaSnakeContextEnvelopeRuntime.SnakeContextEnvelope envelope = contextEnvelopeRuntime.build(
                state.getIdeZone(),
                latestEditorSnapshot,
                latestPredictionEvent,
                resolvedPolicyDecisionRef,
                deniedRefs,
                artifactRefs
        );
        lastContextEnvelopeJson = envelope.toJson();
        lastContextSummary = "zone=" + state.getIdeZone()
                + ", intent=" + latestPredictionEvent.intentKind()
                + ", release_mode=" + contextReleaseMode()
                + ", hidden=" + temporarilyHidden
                + ", dnd=" + uiPreferences.doNotDisturbMode()
                + ", presentation=" + presentationModeActive
                + ", denied=" + String.join("|", deniedRefs);
        return lastContextEnvelopeJson;
    }

    private ClientResponse dispatchEnvelopeToWorker(String envelopeJson) {
        AnantaApiClient apiClient;
        synchronized (lock) {
            if (!hubConnectionConfig.enabled() || hubApiClient == null) {
                state = state.withHubConnectionState("local_only");
                return localOnlyResponse("context_dispatch_skipped");
            }
            if (!canUseCloudProvider()) {
                state = state.withHubConnectionState("local_only");
                lastPolicyReasonCode = "external_provider_denied";
                return policyDeniedResponse("external_provider_denied");
            }
            apiClient = hubApiClient;
        }

        SanitizedEnvelope sanitized = sanitizeEnvelope(envelopeJson);
        ClientResponse response = apiClient.submitGoal(
                "Process Eclipse Snake context envelope",
                sanitized.envelopeJson(),
                "repository_understanding",
                "io.ananta.eclipse.command.snake_context",
                null
        );
        synchronized (lock) {
            if (!sanitized.reasonCodes().isEmpty()) {
                lastPolicyReasonCode = String.join(",", sanitized.reasonCodes());
            } else {
                lastPolicyReasonCode = "none";
            }
            state = response.isOk() ? state.withHubConnectionState("hub_connected") : state.withHubConnectionState(resolveHubFallbackState(response));
            return response;
        }
    }

    private ClientResponse sendHeartbeatLocked() {
        if (!hubConnectionConfig.enabled() || hubApiClient == null || registeredClientId.isBlank()) {
            state = state.withHubConnectionState("local_only");
            return localOnlyResponse("snake_heartbeat_skipped");
        }
        ClientResponse response = hubApiClient.snakeHeartbeat(registeredClientId, workspaceId);
        state = response.isOk() ? state.withHubConnectionState("hub_connected") : state.withHubConnectionState(resolveHubFallbackState(response));
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

    private boolean canUseCloudProvider() {
        if (uiPreferences.privacySettings().allowExternalProviders()) {
            return true;
        }
        if (hubConnectionConfig.baseUrl().isBlank()) {
            return true;
        }
        URI parsed = URI.create(hubConnectionConfig.baseUrl());
        String host = parsed.getHost();
        if (host == null) {
            return true;
        }
        String normalizedHost = host.toLowerCase(Locale.ROOT);
        return normalizedHost.equals("localhost") || normalizedHost.equals("127.0.0.1");
    }

    private static String resolveHubFallbackState(ClientResponse response) {
        if (response != null && response.getState() == DegradedState.BACKEND_UNREACHABLE) {
            return "offline";
        }
        return "local_only";
    }

    private SanitizedEnvelope sanitizeEnvelope(String envelopeJson) {
        String redacted = TokenRedaction.redactSensitiveText(envelopeJson);
        List<String> reasonCodes = new ArrayList<>();
        if (!redacted.equals(envelopeJson)) {
            reasonCodes.add("sensitive_values_redacted");
        }
        return new SanitizedEnvelope(redacted, reasonCodes);
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

    private static ClientResponse policyDeniedResponse(String reasonCode) {
        return new ClientResponse(
                false,
                403,
                DegradedState.POLICY_DENIED,
                "{\"reason_code\":\"" + sanitizeFallback(reasonCode, "policy_denied") + "\"}",
                sanitizeFallback(reasonCode, "policy_denied"),
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

    private record SanitizedEnvelope(
            String envelopeJson,
            List<String> reasonCodes
    ) {
    }
}
