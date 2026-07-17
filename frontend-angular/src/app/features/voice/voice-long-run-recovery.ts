import { InjectionToken } from '@angular/core';

import {
  VoiceLongRunDisplayMode,
  normalizeVoiceLongRunDisplayMode,
} from './voice-long-run-display-mode';
import { VoiceLongRunCreateRequest } from './voice.models';

const RECOVERY_KEY = 'ananta.voice.long_run.recovery.v1';

export interface VoiceLongRunRecoveryMetadata {
  schemaVersion: 1;
  runId: string;
  hubUrl: string;
  createIdempotencyKey: string;
  profileGeneration?: number;
  request: VoiceLongRunCreateRequest;
  /** UI-only preference. Preview text/audio/session capabilities are never persisted. */
  displayMode?: VoiceLongRunDisplayMode;
  nextSequence: number;
  timelineMilliseconds: number;
  completedTimelineMilliseconds?: number;
  durableNextSequence?: number;
  durableTimelineMilliseconds?: number;
  updatedAt: number;
}

export interface VoiceLongRunRecoveryPort {
  load(): VoiceLongRunRecoveryMetadata | null;
  save(metadata: VoiceLongRunRecoveryMetadata): void;
  clear(runId?: string): void;
}

/** Stores only a run cursor/configuration. Audio remains encrypted in IndexedDB. */
export class LocalVoiceLongRunRecoveryStore implements VoiceLongRunRecoveryPort {
  load(): VoiceLongRunRecoveryMetadata | null {
    try {
      const parsed = JSON.parse(localStorage.getItem(RECOVERY_KEY) || 'null') as VoiceLongRunRecoveryMetadata | null;
      if (!parsed || parsed.schemaVersion !== 1 || !parsed.hubUrl || !parsed.createIdempotencyKey
        || !Number.isInteger(parsed.nextSequence) || parsed.nextSequence < 0
        || !Number.isInteger(parsed.timelineMilliseconds) || parsed.timelineMilliseconds < 0) {
        return null;
      }
      return {
        ...parsed,
        displayMode: normalizeVoiceLongRunDisplayMode(parsed.displayMode),
      };
    } catch {
      return null;
    }
  }

  save(metadata: VoiceLongRunRecoveryMetadata): void {
    localStorage.setItem(RECOVERY_KEY, JSON.stringify(metadata));
  }

  clear(runId?: string): void {
    const current = this.load();
    if (!runId || current?.runId === runId) localStorage.removeItem(RECOVERY_KEY);
  }
}

export const VOICE_LONG_RUN_RECOVERY = new InjectionToken<VoiceLongRunRecoveryPort>(
  'VOICE_LONG_RUN_RECOVERY',
  { providedIn: 'root', factory: () => new LocalVoiceLongRunRecoveryStore() },
);
