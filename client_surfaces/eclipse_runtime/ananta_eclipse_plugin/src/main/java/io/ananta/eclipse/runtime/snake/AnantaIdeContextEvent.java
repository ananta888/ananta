package io.ananta.eclipse.runtime.snake;

public record AnantaIdeContextEvent(
        String zone,
        String partId,
        String partTitle,
        long capturedAtEpochMillis
) {
}
