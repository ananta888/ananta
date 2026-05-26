package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaSnakeOverlayCanvasTest {
    @Test
    void canvasDefaultsToInputPassthroughAndOpacityClamps() {
        AnantaSnakeOverlayCanvas canvas = new AnantaSnakeOverlayCanvas();
        assertTrue(canvas.isInputPassthrough());
        canvas.setOpacityPercent(5);
        assertEquals(10, canvas.opacityPercent());
        canvas.setOpacityPercent(150);
        assertEquals(100, canvas.opacityPercent());
    }
}
