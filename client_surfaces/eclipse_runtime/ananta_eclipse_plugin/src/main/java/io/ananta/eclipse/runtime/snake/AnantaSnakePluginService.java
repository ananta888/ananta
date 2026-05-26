package io.ananta.eclipse.runtime.snake;

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

    private AnantaSnakeState state = AnantaSnakeState.initial();
    private ScheduledFuture<?> tickFuture;
    private int configuredActiveTickRateFps = 20;
    private int stillTicks;
    private boolean hasPreviousMousePosition;
    private int previousMouseX;
    private int previousMouseY;
    private AnantaIdeContextEvent latestIdeContextEvent = new AnantaIdeContextEvent("unknown", "", "", 0L);

    public AnantaSnakeState snapshot() {
        synchronized (lock) {
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
                    .withFollowDistance(DEFAULT_FOLLOW_DISTANCE_PX)
                    .withModes("follow_mouse", "observing")
                    .withWorkbenchActive(true)
                    .withTickRate(configuredActiveTickRateFps);
            scheduleTicksLocked();
            return state;
        }
    }

    public AnantaSnakeState stop() {
        synchronized (lock) {
            cancelTicksLocked();
            stillTicks = 0;
            hasPreviousMousePosition = false;
            state = state.withRunning(false).withModes(state.getFollowMode(), "idle");
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
        AnantaIdeZoneRuntime zoneRuntime = new AnantaIdeZoneRuntime();
        AnantaIdeContextEvent event = zoneRuntime.buildEvent(partId, partTitle);
        synchronized (lock) {
            latestIdeContextEvent = event;
            state = state.withIdeZone(event.zone());
            if (!state.isRunning()) {
                state = state.withModes(state.getFollowMode(), contextModeForZone(event.zone()));
            }
            return state;
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
            return state;
        }
    }

    public AnantaSnakeState tickNowForTest() {
        synchronized (lock) {
            tickLocked();
            return state;
        }
    }

    public void shutdown() {
        synchronized (lock) {
            cancelTicksLocked();
            scheduler.shutdownNow();
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
