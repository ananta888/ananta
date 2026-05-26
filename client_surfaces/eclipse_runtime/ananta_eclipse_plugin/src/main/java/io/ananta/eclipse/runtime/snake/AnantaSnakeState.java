package io.ananta.eclipse.runtime.snake;

import java.util.Objects;

public final class AnantaSnakeState {
    private final boolean enabled;
    private final boolean running;
    private final boolean overlayVisible;
    private final String followMode;
    private final String contextMode;
    private final String ideZone;
    private final String hubConnectionState;
    private final int tickRateFps;
    private final int followDistancePx;
    private final boolean workbenchActive;
    private final int mouseX;
    private final int mouseY;
    private final int overlayX;
    private final int overlayY;

    public AnantaSnakeState(
            boolean enabled,
            boolean running,
            boolean overlayVisible,
            String followMode,
            String contextMode,
            String ideZone,
            String hubConnectionState,
            int tickRateFps,
            int followDistancePx,
            boolean workbenchActive,
            int mouseX,
            int mouseY,
            int overlayX,
            int overlayY
    ) {
        this.enabled = enabled;
        this.running = running;
        this.overlayVisible = overlayVisible;
        this.followMode = Objects.requireNonNull(followMode, "followMode");
        this.contextMode = Objects.requireNonNull(contextMode, "contextMode");
        this.ideZone = Objects.requireNonNull(ideZone, "ideZone");
        this.hubConnectionState = Objects.requireNonNull(hubConnectionState, "hubConnectionState");
        this.tickRateFps = Math.max(1, tickRateFps);
        this.followDistancePx = Math.max(1, followDistancePx);
        this.workbenchActive = workbenchActive;
        this.mouseX = mouseX;
        this.mouseY = mouseY;
        this.overlayX = overlayX;
        this.overlayY = overlayY;
    }

    public static AnantaSnakeState initial() {
        return new AnantaSnakeState(
                false,
                false,
                false,
                "follow_mouse",
                "idle",
                "unknown",
                "offline",
                20,
                24,
                true,
                0,
                0,
                0,
                0
        );
    }

    public boolean isEnabled() {
        return enabled;
    }

    public boolean isRunning() {
        return running;
    }

    public boolean isOverlayVisible() {
        return overlayVisible;
    }

    public String getFollowMode() {
        return followMode;
    }

    public String getContextMode() {
        return contextMode;
    }

    public String getIdeZone() {
        return ideZone;
    }

    public String getHubConnectionState() {
        return hubConnectionState;
    }

    public int getTickRateFps() {
        return tickRateFps;
    }

    public int getFollowDistancePx() {
        return followDistancePx;
    }

    public boolean isWorkbenchActive() {
        return workbenchActive;
    }

    public int getMouseX() {
        return mouseX;
    }

    public int getMouseY() {
        return mouseY;
    }

    public int getOverlayX() {
        return overlayX;
    }

    public int getOverlayY() {
        return overlayY;
    }

    public AnantaSnakeState withEnabled(boolean next) {
        boolean visible = next && running;
        return new AnantaSnakeState(
                next,
                running,
                visible,
                followMode,
                contextMode,
                ideZone,
                hubConnectionState,
                tickRateFps,
                followDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withRunning(boolean next) {
        boolean visible = enabled && next;
        return new AnantaSnakeState(
                enabled,
                next,
                visible,
                followMode,
                contextMode,
                ideZone,
                hubConnectionState,
                tickRateFps,
                followDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withModes(String nextFollowMode, String nextContextMode) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                nextFollowMode,
                nextContextMode,
                ideZone,
                hubConnectionState,
                tickRateFps,
                followDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withIdeZone(String nextIdeZone) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                followMode,
                contextMode,
                nextIdeZone,
                hubConnectionState,
                tickRateFps,
                followDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withHubConnectionState(String nextHubConnectionState) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                followMode,
                contextMode,
                ideZone,
                nextHubConnectionState,
                tickRateFps,
                followDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withTickRate(int nextTickRateFps) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                followMode,
                contextMode,
                ideZone,
                hubConnectionState,
                nextTickRateFps,
                followDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withFollowDistance(int nextFollowDistancePx) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                followMode,
                contextMode,
                ideZone,
                hubConnectionState,
                tickRateFps,
                nextFollowDistancePx,
                workbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withWorkbenchActive(boolean nextWorkbenchActive) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                followMode,
                contextMode,
                ideZone,
                hubConnectionState,
                tickRateFps,
                followDistancePx,
                nextWorkbenchActive,
                mouseX,
                mouseY,
                overlayX,
                overlayY
        );
    }

    public AnantaSnakeState withMouseAndOverlay(int nextMouseX, int nextMouseY, int nextOverlayX, int nextOverlayY) {
        return new AnantaSnakeState(
                enabled,
                running,
                overlayVisible,
                followMode,
                contextMode,
                ideZone,
                hubConnectionState,
                tickRateFps,
                followDistancePx,
                workbenchActive,
                nextMouseX,
                nextMouseY,
                nextOverlayX,
                nextOverlayY
        );
    }
}
