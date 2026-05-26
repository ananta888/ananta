package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaSnakePluginDescriptorTest {
    @Test
    void pluginDescriptorContainsSnakeCommandViewAndMenuEntries() throws IOException {
        String pluginXml = Files.readString(Path.of("plugin.xml"));
        assertTrue(pluginXml.contains("io.ananta.eclipse.command.snake_toggle"));
        assertTrue(pluginXml.contains("io.ananta.eclipse.view.snake"));
        assertTrue(pluginXml.contains("io.ananta.eclipse.menu.snake"));
        assertTrue(pluginXml.contains("io.ananta.eclipse.toolbar.snake"));
    }

    @Test
    void manifestKeepsLazyActivationPolicy() throws IOException {
        String manifest = Files.readString(Path.of("META-INF", "MANIFEST.MF"));
        assertTrue(manifest.contains("Bundle-ActivationPolicy: lazy"));
    }
}
