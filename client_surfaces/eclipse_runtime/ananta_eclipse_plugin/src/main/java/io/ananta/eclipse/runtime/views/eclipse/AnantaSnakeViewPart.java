package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.snake.AnantaSnakeOverlayCanvas;
import io.ananta.eclipse.runtime.snake.AnantaSnakeOverlayModel;
import io.ananta.eclipse.runtime.snake.AnantaSnakePredictionEvent;
import io.ananta.eclipse.runtime.snake.AnantaSnakeState;

import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Label;
import org.eclipse.ui.dialogs.PreferencesUtil;
import org.eclipse.ui.part.ViewPart;

public final class AnantaSnakeViewPart extends ViewPart {
    public static final String VIEW_ID = "io.ananta.eclipse.view.snake";

    private final AnantaSnakeOverlayCanvas overlayCanvas = new AnantaSnakeOverlayCanvas();
    private Label statusLabel;
    private Label predictionLabel;
    private Label hubConfigLabel;
    private Label contextStatusLabel;
    private Label askResultLabel;
    private Button toggleButton;
    private Button askButton;
    private Button resetContextButton;
    private Button openSettingsButton;

    @Override
    public void createPartControl(Composite parent) {
        parent.setLayout(new GridLayout(1, false));

        Label title = new Label(parent, SWT.WRAP);
        title.setText("Ananta Snake Overlay");
        title.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        statusLabel = new Label(parent, SWT.WRAP);
        statusLabel.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        toggleButton = new Button(parent, SWT.PUSH);
        toggleButton.setLayoutData(new GridData(SWT.LEFT, SWT.TOP, false, false));
        toggleButton.addListener(SWT.Selection, event -> toggleSnake());

        askButton = new Button(parent, SWT.PUSH);
        askButton.setLayoutData(new GridData(SWT.LEFT, SWT.TOP, false, false));
        askButton.setText("Ask Ananta Snake");
        askButton.addListener(SWT.Selection, event -> requestAsk());

        resetContextButton = new Button(parent, SWT.PUSH);
        resetContextButton.setLayoutData(new GridData(SWT.LEFT, SWT.TOP, false, false));
        resetContextButton.setText("Reset Context");
        resetContextButton.addListener(SWT.Selection, event -> resetContext());

        openSettingsButton = new Button(parent, SWT.PUSH);
        openSettingsButton.setLayoutData(new GridData(SWT.LEFT, SWT.TOP, false, false));
        openSettingsButton.setText("Open Settings");
        openSettingsButton.addListener(SWT.Selection, event -> openSettings());

        predictionLabel = new Label(parent, SWT.WRAP);
        predictionLabel.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        hubConfigLabel = new Label(parent, SWT.WRAP);
        hubConfigLabel.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        contextStatusLabel = new Label(parent, SWT.WRAP);
        contextStatusLabel.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        askResultLabel = new Label(parent, SWT.WRAP);
        askResultLabel.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        Composite overlayContainer = new Composite(parent, SWT.BORDER);
        overlayContainer.setLayoutData(new GridData(SWT.FILL, SWT.FILL, true, true));
        overlayContainer.setLayout(new FillLayout());
        overlayCanvas.create(overlayContainer);

        refreshUi(AnantaRuntimeBootstrap.snakeService().snapshot());
    }

    @Override
    public void setFocus() {
        if (toggleButton != null && !toggleButton.isDisposed()) {
            toggleButton.setFocus();
        }
    }

    @Override
    public void dispose() {
        overlayCanvas.dispose();
        super.dispose();
    }

    private void toggleSnake() {
        AnantaSnakeState state = AnantaRuntimeBootstrap.snakeService().toggleEnabled();
        refreshUi(state);
    }

    private void requestAsk() {
        askResultLabel.setText("ask=running");
        Thread thread = new Thread(() -> {
            AnantaRuntimeBootstrap.snakeService().askAnantaSnakeNow("Explain current IDE context");
            Display.getDefault().asyncExec(() -> refreshUi(AnantaRuntimeBootstrap.snakeService().snapshot()));
        }, "ananta-snake-ask-action");
        thread.setDaemon(true);
        thread.start();
    }

    private void resetContext() {
        AnantaRuntimeBootstrap.snakeService().resetContextAuthorization();
        refreshUi(AnantaRuntimeBootstrap.snakeService().snapshot());
    }

    private void openSettings() {
        PreferencesUtil.createPreferenceDialogOn(
                getSite().getShell(),
                "io.ananta.eclipse.preferences",
                new String[]{"io.ananta.eclipse.preferences"},
                null
        ).open();
    }

    private void refreshUi(AnantaSnakeState state) {
        String message = "enabled=" + state.isEnabled()
                + ", running=" + state.isRunning()
                + ", follow_mode=" + state.getFollowMode()
                + ", context_mode=" + state.getContextMode()
                + ", zone=" + state.getIdeZone()
                + ", follow_distance=" + state.getFollowDistancePx()
                + ", tick_fps=" + state.getTickRateFps()
                + ", workbench_active=" + state.isWorkbenchActive()
                + ", hub=" + state.getHubConnectionState();
        statusLabel.setText(message);
        toggleButton.setText(state.isEnabled() ? "Disable Snake" : "Enable Snake");
        AnantaSnakePredictionEvent prediction = AnantaRuntimeBootstrap.snakeService().latestPredictionEvent();
        predictionLabel.setText("prediction=intent=" + prediction.intentKind()
                + ", confidence=" + prediction.confidence()
                + ", evidence=" + String.join("|", prediction.evidence()));
        var hubConfig = AnantaRuntimeBootstrap.snakeService().hubConnectionConfig();
        hubConfigLabel.setText("hub_config=enabled=" + hubConfig.enabled()
                + ", base_url=" + hubConfig.baseUrl()
                + ", auth_mode=" + hubConfig.authMode()
                + ", timeout_seconds=" + hubConfig.timeoutSeconds());
        contextStatusLabel.setText("context=release_mode=" + AnantaRuntimeBootstrap.snakeService().contextReleaseMode()
                + ", policy_reason=" + AnantaRuntimeBootstrap.snakeService().lastPolicyReasonCode()
                + ", summary=" + AnantaRuntimeBootstrap.snakeService().lastContextSummary());
        askResultLabel.setText("ask_result=" + AnantaRuntimeBootstrap.snakeService().lastAskResult());
        overlayCanvas.setOpacityPercent(AnantaRuntimeBootstrap.snakeService().overlayOpacityPercent());
        overlayCanvas.setModel(AnantaSnakeOverlayModel.fromState(state));
    }
}
