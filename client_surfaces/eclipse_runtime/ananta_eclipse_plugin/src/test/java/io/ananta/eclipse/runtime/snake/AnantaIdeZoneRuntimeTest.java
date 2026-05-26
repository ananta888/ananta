package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaIdeZoneRuntimeTest {
    @Test
    void zoneMappingDetectsKnownWorkbenchAreas() {
        AnantaIdeZoneRuntime runtime = new AnantaIdeZoneRuntime();
        assertEquals("editor", runtime.detectZone("org.eclipse.ui.editors"));
        assertEquals("problems", runtime.detectZone("org.eclipse.ui.views.ProblemView"));
        assertEquals("console", runtime.detectZone("org.eclipse.ui.console.ConsoleView"));
        assertEquals("package_explorer", runtime.detectZone("org.eclipse.jdt.ui.PackageExplorer"));
        assertEquals("git_compare", runtime.detectZone("org.eclipse.compare.CompareEditor"));
        assertEquals("unknown", runtime.detectZone("io.ananta.custom.UnknownPart"));
    }

    @Test
    void ideContextEventsStoreNormalizedZone() {
        AnantaIdeZoneRuntime runtime = new AnantaIdeZoneRuntime();
        AnantaIdeContextEvent event = runtime.buildEvent("org.eclipse.ui.console.ConsoleView", "Console");
        assertEquals("console", event.zone());
        assertEquals("org.eclipse.ui.console.ConsoleView", event.partId());
        assertTrue(event.capturedAtEpochMillis() > 0);
    }
}
