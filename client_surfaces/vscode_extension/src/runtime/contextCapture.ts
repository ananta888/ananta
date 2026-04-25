import { redactSensitiveText } from "./redaction";

const DEFAULT_MAX_SELECTION_CHARS = 2000;
const DEFAULT_MAX_EXCERPT_CHARS = 2000;
const DEFAULT_PREVIEW_CHARS = 280;

const WARNING_PATTERNS: RegExp[] = [/api[_-]?key/i, /secret/i, /private[_-]?key/i, /password/i, /token/i];
const BLOCK_PATTERNS: RegExp[] = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /AKIA[0-9A-Z]{16}/,
  /xox[baprs]-[A-Za-z0-9-]{10,}/
];

export interface RawEditorContextInput {
  filePath: string | null;
  projectRoot: string | null;
  languageId: string | null;
  selectionText: string | null;
  fileContentExcerpt: string | null;
}

export interface ContextCaptureBounds {
  maxSelectionChars?: number;
  maxExcerptChars?: number;
  maxPreviewChars?: number;
}

export interface BoundedContextPayload {
  schema: "client_bounded_context_payload_v1";
  file_path: string | null;
  project_root: string | null;
  language_id: string | null;
  selection_text: string | null;
  file_content_excerpt: string | null;
  selection_clipped: boolean;
  file_content_clipped: boolean;
  warnings: string[];
  blocked_reasons: string[];
  bounded: true;
  implicit_unrelated_paths_included: false;
  provenance: {
    has_selection: boolean;
    has_file_path: boolean;
    has_project_root: boolean;
  };
}

export interface ContextPreview {
  filePath: string | null;
  projectRoot: string | null;
  languageId: string | null;
  selectionLength: number;
  selectionExcerpt: string | null;
  fileExcerpt: string | null;
  selectionClipped: boolean;
  fileContentClipped: boolean;
  warnings: string[];
  blockedReasons: string[];
}

export interface PackagedEditorContext {
  payload: BoundedContextPayload;
  preview: ContextPreview;
}

function clean(value: string | null | undefined, maxChars: number): string {
  return String(value || "")
    .trim()
    .slice(0, maxChars);
}

function clip(value: string, maxChars: number): { value: string; clipped: boolean } {
  const clipped = value.length > maxChars;
  return {
    value: clipped ? value.slice(0, maxChars) : value,
    clipped
  };
}

function detectWarnings(...chunks: Array<string | null>): string[] {
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

function detectBlocks(...chunks: Array<string | null>): string[] {
  const merged = chunks
    .map((chunk) => String(chunk || ""))
    .join("\n")
    .trim();
  if (!merged) {
    return [];
  }
  const reasons: string[] = [];
  for (const pattern of BLOCK_PATTERNS) {
    if (pattern.test(merged)) {
      reasons.push("selection_contains_high_risk_secret");
      break;
    }
  }
  return reasons;
}

export function packageEditorContext(
  input: RawEditorContextInput,
  bounds: ContextCaptureBounds = {}
): PackagedEditorContext {
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

  const redactedSelection = redactSensitiveText(clippedSelection.value);
  const redactedExcerpt = redactSensitiveText(clippedExcerpt.value);
  const selectionPreview = redactedSelection.slice(0, maxPreviewChars);
  const excerptPreview = redactedExcerpt.slice(0, maxPreviewChars);

  const payload: BoundedContextPayload = {
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
