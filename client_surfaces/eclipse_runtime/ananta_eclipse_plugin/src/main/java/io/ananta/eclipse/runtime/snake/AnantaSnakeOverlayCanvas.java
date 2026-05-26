package io.ananta.eclipse.runtime.snake;

import org.eclipse.swt.SWT;
import org.eclipse.swt.events.PaintEvent;
import org.eclipse.swt.events.PaintListener;
import org.eclipse.swt.graphics.Color;
import org.eclipse.swt.graphics.GC;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.widgets.Canvas;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Display;

public final class AnantaSnakeOverlayCanvas {
    private Canvas canvas;
    private PaintListener paintListener;
    private AnantaSnakeOverlayModel model = AnantaSnakeOverlayModel.fromState(AnantaSnakeState.initial());
    private int opacityPercent = 60;

    public void create(Composite parent) {
        if (canvas != null && !canvas.isDisposed()) {
            return;
        }
        canvas = new Canvas(parent, SWT.DOUBLE_BUFFERED | SWT.NO_BACKGROUND | SWT.NO_FOCUS);
        canvas.setLayoutData(new GridData(SWT.FILL, SWT.FILL, true, true));
        canvas.setEnabled(false);
        paintListener = this::render;
        canvas.addPaintListener(paintListener);
    }

    public void setModel(AnantaSnakeOverlayModel nextModel) {
        model = nextModel == null ? AnantaSnakeOverlayModel.fromState(AnantaSnakeState.initial()) : nextModel;
        if (canvas != null && !canvas.isDisposed()) {
            canvas.redraw();
        }
    }

    public void dispose() {
        if (canvas != null && !canvas.isDisposed()) {
            if (paintListener != null) {
                canvas.removePaintListener(paintListener);
            }
            canvas.dispose();
        }
        canvas = null;
        paintListener = null;
    }

    public void setOpacityPercent(int nextOpacityPercent) {
        opacityPercent = Math.max(10, Math.min(100, nextOpacityPercent));
        if (canvas != null && !canvas.isDisposed()) {
            canvas.redraw();
        }
    }

    public int opacityPercent() {
        return opacityPercent;
    }

    public boolean isInputPassthrough() {
        return canvas == null || !canvas.getEnabled();
    }

    private void render(PaintEvent event) {
        if (model == null || model.segments().isEmpty()) {
            return;
        }
        GC gc = event.gc;
        gc.setAntialias(SWT.ON);
        gc.setAlpha((int) Math.round(255.0 * (opacityPercent / 100.0)));
        Display display = event.display;
        Color snakeColor = display.getSystemColor(SWT.COLOR_DARK_GREEN);
        gc.setForeground(snakeColor);
        gc.setLineWidth(3);

        for (int idx = 1; idx < model.segments().size(); idx++) {
            AnantaSnakeOverlayModel.Segment previous = model.segments().get(idx - 1);
            AnantaSnakeOverlayModel.Segment current = model.segments().get(idx);
            gc.drawLine(previous.x(), previous.y(), current.x(), current.y());
        }
        AnantaSnakeOverlayModel.Segment head = model.segments().get(0);
        gc.fillOval(head.x() - 4, head.y() - 4, 8, 8);
    }
}
