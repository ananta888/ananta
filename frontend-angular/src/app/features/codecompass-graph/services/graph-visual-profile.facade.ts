import { computed, Inject, Injectable, signal } from '@angular/core';
import {
  canonicalGraphVisualProfileJson,
  DEFAULT_GRAPH_VISUAL_PROFILE,
  GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES,
  GRAPH_VISUAL_PROFILE_PRESETS,
  GraphVisualProfile,
  GraphVisualProfilePresetId,
  graphVisualProfileHash,
} from '../models/graph-visual-profile.model';
import {
  GRAPH_VISUAL_PROFILE_STORAGE,
} from './local-storage-graph-visual-profile.adapter';
import { GraphVisualProfileStoragePort } from './graph-visual-profile-storage.port';
import {
  GraphVisualProfileValidationIssue,
  GraphVisualProfileValidationResult,
  GraphVisualProfileValidator,
} from './graph-visual-profile-validator';

export interface GraphVisualProfileOperationResult {
  ok: boolean;
  profile: GraphVisualProfile;
  issues: readonly GraphVisualProfileValidationIssue[];
}

function operationIssue(reasonCode: string, message: string): GraphVisualProfileValidationIssue {
  return { path: '$', reasonCode, message };
}

/**
 * Viewer-local state facade. Consumers must provide it at GraphViewer scope;
 * the injected storage adapter may remain shared.
 */
@Injectable()
export class GraphVisualProfileFacade {
  readonly presets = GRAPH_VISUAL_PROFILE_PRESETS;
  readonly activeProfile = signal<GraphVisualProfile>(DEFAULT_GRAPH_VISUAL_PROFILE);
  readonly profileHash = computed(() => graphVisualProfileHash(this.activeProfile()));
  readonly lastIssues = signal<readonly GraphVisualProfileValidationIssue[]>([]);
  readonly contextKey = signal('');

  private readonly validator = new GraphVisualProfileValidator();

  constructor(
    @Inject(GRAPH_VISUAL_PROFILE_STORAGE)
    private readonly storage: GraphVisualProfileStoragePort,
  ) {}

  load(contextKey: string): GraphVisualProfileOperationResult {
    this.contextKey.set(contextKey.trim());
    const stored = this.storage.load(this.contextKey());
    if (!stored.ok) {
      return this.fallback(stored.reasonCode ?? 'storage_read_failed', 'Stored profile could not be read.');
    }
    if (stored.value === null) {
      this.activeProfile.set(DEFAULT_GRAPH_VISUAL_PROFILE);
      this.lastIssues.set([]);
      return { ok: true, profile: this.activeProfile(), issues: [] };
    }
    const parsed = this.parseImport(stored.value);
    if (!parsed.valid || !parsed.profile) {
      this.activeProfile.set(DEFAULT_GRAPH_VISUAL_PROFILE);
      this.lastIssues.set(parsed.issues);
      return { ok: false, profile: this.activeProfile(), issues: parsed.issues };
    }
    this.activeProfile.set(parsed.profile);
    this.lastIssues.set([]);
    return { ok: true, profile: parsed.profile, issues: [] };
  }

  activate(profile: unknown): GraphVisualProfileOperationResult {
    const validation = this.validator.validate(profile);
    if (!validation.valid || !validation.profile) {
      this.lastIssues.set(validation.issues);
      return { ok: false, profile: this.activeProfile(), issues: validation.issues };
    }
    this.activeProfile.set(validation.profile);
    this.lastIssues.set([]);
    return { ok: true, profile: validation.profile, issues: [] };
  }

  selectPreset(presetId: GraphVisualProfilePresetId): GraphVisualProfileOperationResult {
    return this.activate(this.presets[presetId]);
  }

  save(): GraphVisualProfileOperationResult {
    if (!this.contextKey()) {
      const issue = operationIssue('context_not_set', 'A graph context must be loaded before saving.');
      this.lastIssues.set([issue]);
      return { ok: false, profile: this.activeProfile(), issues: [issue] };
    }
    const serialized = canonicalGraphVisualProfileJson(this.activeProfile());
    const saved = this.storage.save(this.contextKey(), serialized);
    if (!saved.ok) {
      const issue = operationIssue(saved.reasonCode ?? 'storage_write_failed', 'Profile could not be saved.');
      this.lastIssues.set([issue]);
      return { ok: false, profile: this.activeProfile(), issues: [issue] };
    }
    this.lastIssues.set([]);
    return { ok: true, profile: this.activeProfile(), issues: [] };
  }

  reset(): GraphVisualProfileOperationResult {
    const context = this.contextKey();
    if (context) {
      const removed = this.storage.remove(context);
      if (!removed.ok) {
        const issue = operationIssue(removed.reasonCode ?? 'storage_remove_failed', 'Stored override could not be removed.');
        this.lastIssues.set([issue]);
        return { ok: false, profile: this.activeProfile(), issues: [issue] };
      }
    }
    this.activeProfile.set(DEFAULT_GRAPH_VISUAL_PROFILE);
    this.lastIssues.set([]);
    return { ok: true, profile: this.activeProfile(), issues: [] };
  }

  importProfile(serialized: string): GraphVisualProfileOperationResult {
    const validation = this.parseImport(serialized);
    if (!validation.valid || !validation.profile) {
      this.lastIssues.set(validation.issues);
      return { ok: false, profile: this.activeProfile(), issues: validation.issues };
    }
    this.activeProfile.set(validation.profile);
    this.lastIssues.set([]);
    return { ok: true, profile: validation.profile, issues: [] };
  }

  async importProfileFile(
    file: Pick<Blob, 'size' | 'text'>,
  ): Promise<GraphVisualProfileOperationResult> {
    if (!file || !Number.isFinite(file.size) || file.size < 0) {
      return this.rejectImport('invalid_import_file', 'Imported profile file is invalid.');
    }
    // Do not allocate or decode attacker-controlled content before this check.
    if (file.size > GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES) {
      return this.rejectImport('import_too_large', 'Imported profile exceeds the size limit.');
    }
    try {
      return this.importProfile(await file.text());
    } catch {
      return this.rejectImport('import_read_failed', 'Imported profile could not be read.');
    }
  }

  exportProfile(): string {
    return canonicalGraphVisualProfileJson(this.activeProfile());
  }

  private parseImport(serialized: string): GraphVisualProfileValidationResult {
    if (typeof serialized !== 'string') {
      return { valid: false, issues: [operationIssue('import_not_string', 'Imported profile must be JSON text.')] };
    }
    if (new TextEncoder().encode(serialized).byteLength > GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES) {
      return { valid: false, issues: [operationIssue('import_too_large', 'Imported profile exceeds the size limit.')] };
    }
    try {
      return this.validator.validate(JSON.parse(serialized));
    } catch {
      return { valid: false, issues: [operationIssue('invalid_json', 'Imported profile is not valid JSON.')] };
    }
  }

  private fallback(reasonCode: string, message: string): GraphVisualProfileOperationResult {
    const issue = operationIssue(reasonCode, message);
    this.activeProfile.set(DEFAULT_GRAPH_VISUAL_PROFILE);
    this.lastIssues.set([issue]);
    return { ok: false, profile: this.activeProfile(), issues: [issue] };
  }

  private rejectImport(reasonCode: string, message: string): GraphVisualProfileOperationResult {
    const issue = operationIssue(reasonCode, message);
    this.lastIssues.set([issue]);
    return { ok: false, profile: this.activeProfile(), issues: [issue] };
  }
}
