"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.packageEditorContext = packageEditorContext;
const redaction_1 = require("./redaction");
const DEFAULT_MAX_SELECTION_CHARS = 2000;
const DEFAULT_MAX_EXCERPT_CHARS = 2000;
const DEFAULT_PREVIEW_CHARS = 280;
const WARNING_PATTERNS = [/api[_-]?key/i, /secret/i, /private[_-]?key/i, /password/i, /token/i];
const BLOCK_PATTERNS = [
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
    /AKIA[0-9A-Z]{16}/,
    /xox[baprs]-[A-Za-z0-9-]{10,}/
];
function clean(value, maxChars) {
    return String(value || "")
        .trim()
        .slice(0, maxChars);
}
function clip(value, maxChars) {
    const clipped = value.length > maxChars;
    return {
        value: clipped ? value.slice(0, maxChars) : value,
        clipped
    };
}
function detectWarnings(...chunks) {
    const merged = chunks
        .map((chunk) => String(chunk || ""))
        .join("\n")
        .trim();
    if (!merged) {
        return [];
    }
    for (const pattern of WARNING_PATTERNS) {
        if (pattern.test(merged)) {
            return ["selection_may_contain_secret"];
        }
    }
    return [];
}
function detectBlocks(...chunks) {
    const merged = chunks
        .map((chunk) => String(chunk || ""))
        .join("\n")
        .trim();
    if (!merged) {
        return [];
    }
    const reasons = [];
    for (const pattern of BLOCK_PATTERNS) {
        if (pattern.test(merged)) {
            reasons.push("selection_contains_high_risk_secret");
            break;
        }
    }
    return reasons;
}
function packageEditorContext(input, bounds = {}) {
    const maxSelectionChars = Math.max(128, Math.trunc(bounds.maxSelectionChars ?? DEFAULT_MAX_SELECTION_CHARS));
    const maxExcerptChars = Math.max(128, Math.trunc(bounds.maxExcerptChars ?? DEFAULT_MAX_EXCERPT_CHARS));
    const maxPreviewChars = Math.max(80, Math.trunc(bounds.maxPreviewChars ?? DEFAULT_PREVIEW_CHARS));
    const normalizedFilePath = clean(input.filePath, 400) || null;
    const normalizedProjectRoot = clean(input.projectRoot, 400) || null;
    const normalizedLanguage = clean(input.languageId, 120) || null;
    const rawSelection = clean(input.selectionText, maxSelectionChars * 2);
    const rawFileExcerpt = clean(input.fileContentExcerpt, maxExcerptChars * 2);
    const clippedSelection = clip(rawSelection, maxSelectionChars);
    const clippedExcerpt = clip(rawFileExcerpt, maxExcerptChars);
    const warnings = detectWarnings(clippedSelection.value, clippedExcerpt.value, normalizedFilePath);
    const blockedReasons = detectBlocks(clippedSelection.value, clippedExcerpt.value);
    const redactedSelection = (0, redaction_1.redactSensitiveText)(clippedSelection.value);
    const redactedExcerpt = (0, redaction_1.redactSensitiveText)(clippedExcerpt.value);
    const selectionPreview = redactedSelection.slice(0, maxPreviewChars);
    const excerptPreview = redactedExcerpt.slice(0, maxPreviewChars);
    const payload = {
        schema: "client_bounded_context_payload_v1",
        file_path: normalizedFilePath,
        project_root: normalizedProjectRoot,
        language_id: normalizedLanguage,
        selection_text: redactedSelection || null,
        file_content_excerpt: redactedExcerpt || null,
        selection_clipped: clippedSelection.clipped,
        file_content_clipped: clippedExcerpt.clipped,
        warnings,
        blocked_reasons: blockedReasons,
        bounded: true,
        implicit_unrelated_paths_included: false,
        provenance: {
            has_selection: redactedSelection.length > 0,
            has_file_path: normalizedFilePath !== null,
            has_project_root: normalizedProjectRoot !== null
        }
    };
    return {
        payload,
        preview: {
            filePath: normalizedFilePath,
            projectRoot: normalizedProjectRoot,
            languageId: normalizedLanguage,
            selectionLength: redactedSelection.length,
            selectionExcerpt: selectionPreview || null,
            fileExcerpt: excerptPreview || null,
            selectionClipped: clippedSelection.clipped,
            fileContentClipped: clippedExcerpt.clipped,
            warnings,
            blockedReasons
        }
    };
}
//# sourceMappingURL=contextCapture.js.map