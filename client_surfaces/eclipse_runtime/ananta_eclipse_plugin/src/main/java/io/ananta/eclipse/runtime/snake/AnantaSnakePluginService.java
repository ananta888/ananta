package io.ananta.eclipse.runtime.snake;

import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public final class AnantaSnakePluginService {
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
            state = state.withEnabled(true).withRunning(true).withModes(state.getFollowMode(), "observing");
            scheduleTicksLocked();
            return state;
        }
    }

    public AnantaSnakeState stop() {
        synchronized (lock) {
            cancelTicksLocked();
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

    public AnantaSnakeState updateMousePosition(
            AnantaMouseTrackingRuntime.Point mousePoint,
            AnantaMouseTrackingRuntime.Bounds sourceBounds,
            AnantaMouseTrackingRuntime.Bounds overlayBounds
    ) {
        AnantaMouseTrackingRuntime runtime = new AnantaMouseTrackingRuntime();
        AnantaMouseTrackingRuntime.Point normalized = runtime.normalizePoint(mousePoint, sourceBounds, overlayBounds);
        synchronized (lock) {
            state = state.withMouseAndOverlay(mousePoint.x(), mousePoint.y(), normalized.x(), normalized.y());
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
            if (!state.isRunning() || "paused".equals(state.getFollowMode())) {
                return;
            }
            int nextX = state.getOverlayX();
            int nextY = state.getOverlayY();
            int deltaX = state.getMouseX() - nextX;
            int deltaY = state.getMouseY() - nextY;
            nextX += Integer.compare(deltaX, 0) * Math.min(3, Math.abs(deltaX));
            nextY += Integer.compare(deltaY, 0) * Math.min(3, Math.abs(deltaY));
            state = state.withMouseAndOverlay(state.getMouseX(), state.getMouseY(), nextX, nextY);
        }
    }

    private static String normalize(String value) {
        return value == null ? "" : value.trim().toLowerCase();
    }
}
