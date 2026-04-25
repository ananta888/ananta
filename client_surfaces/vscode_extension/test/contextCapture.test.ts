import { describe, expect, it } from "vitest";
import { packageEditorContext } from "../src/runtime/contextCapture";

describe("context capture", () => {
  it("captures bounded payload with selection metadata", () => {
    const packaged = packageEditorContext(
      {
        filePath: "/workspace/src/main.ts",
        projectRoot: "/workspace",
        languageId: "typescript",
        selectionText: "x".repeat(260),
        fileContentExcerpt: "y".repeat(300)
      },
      { maxSelectionChars: 128, maxExcerptChars: 128, maxPreviewChars: 8 }
    );

    expect(packaged.payload.schema).toBe("client_bounded_context_payload_v1");
    expect(packaged.payload.selection_clipped).toBe(true);
    expect(packaged.payload.file_content_clipped).toBe(true);
    expect(packaged.payload.implicit_unrelated_paths_included).toBe(false);
    expect(packaged.preview.languageId).toBe("typescript");
  });

  it("marks secret-like context with warnings", () => {
    const packaged = packageEditorContext({
      filePath: "/workspace/.env",
      projectRoot: "/workspace",
      languageId: "dotenv",
      selectionText: "API_KEY=secret-token",
      fileContentExcerpt: "API_KEY=secret-token"
    });
    expect(packaged.payload.warnings).toContain("selection_may_contain_secret");
    expect(packaged.payload.selection_text).toContain("***");
  });

  it("blocks high-risk secrets", () => {
    const packaged = packageEditorContext({
      filePath: "/workspace/secrets.pem",
      projectRoot: "/workspace",
      languageId: "plaintext",
      selectionText: "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
      fileContentExcerpt: ""
    });
    expect(packaged.preview.blockedReasons).toContain("selection_contains_high_risk_secret");
  });
});
