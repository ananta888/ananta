package io.ananta.eclipse.runtime.snake;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AnantaEditorContextSnapshotRuntimeTest {
    @Test
    void metadataSnapshotContainsOnlyHashedPathAndNoFileContent() {
        AnantaEditorContextSnapshotRuntime runtime = new AnantaEditorContextSnapshotRuntime();
        AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot snapshot = runtime.captureSnapshot(
                "demo-project",
                "/workspace/src/main/java/App.java",
                "java_editor",
                new AnantaEditorContextSnapshotRuntime.SelectionRange(10, 30)
        );
        assertEquals("demo-project", snapshot.projectName());
        assertTrue(snapshot.filePathRef().startsWith("path_ref:"));
        assertEquals("java_editor", snapshot.editorType());
        assertEquals(10, snapshot.selectionRange().startOffset());
        assertEquals(30, snapshot.selectionRange().endOffset());
        assertEquals("metadata_only", snapshot.sourceKind());
        assertFalse(snapshot.includesFileContent());
    }

    @Test
    void selectionRangeValidationRejectsNegativeOrInvertedRanges() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new AnantaEditorContextSnapshotRuntime.SelectionRange(-1, 0)
        );
        assertThrows(
                IllegalArgumentException.class,
                () -> new AnantaEditorContextSnapshotRuntime.SelectionRange(5, 4)
        );
    }
}
