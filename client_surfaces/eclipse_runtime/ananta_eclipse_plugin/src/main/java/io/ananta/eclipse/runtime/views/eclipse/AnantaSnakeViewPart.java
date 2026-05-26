package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.snake.AnantaSnakeOverlayCanvas;
import io.ananta.eclipse.runtime.snake.AnantaSnakeOverlayModel;
import io.ananta.eclipse.runtime.snake.AnantaSnakeState;

import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Label;
import org.eclipse.ui.part.ViewPart;

public final class AnantaSnakeViewPart extends ViewPart {
    public static final String VIEW_ID = "io.ananta.eclipse.view.snake";

    private final AnantaSnakeOverlayCanvas overlayCanvas = new AnantaSnakeOverlayCanvas();
    private Label statusLabel;
    private Button toggleButton;

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

    private void refreshUi(AnantaSnakeState state) {
        String message = "enabled=" + state.isEnabled()
                + ", running=" + state.isRunning()
                + ", follow_mode=" + state.getFollowMode()
                + ", context_mode=" + state.getContextMode()
                + ", hub=" + state.getHubConnectionState();
        statusLabel.setText(message);
        toggleButton.setText(state.isEnabled() ? "Disable Snake" : "Enable Snake");
        overlayCanvas.setModel(AnantaSnakeOverlayModel.fromState(state));
    }
}
