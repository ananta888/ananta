import { LocalVoiceLongRunRecoveryStore } from './voice-long-run-recovery';

describe('LocalVoiceLongRunRecoveryStore', () => {
  beforeEach(() => localStorage.clear());

  it('persists only discovery/configuration metadata without audio plaintext', () => {
    const store = new LocalVoiceLongRunRecoveryStore();
    store.save({
      schemaVersion: 1,
      runId: 'run-a',
      hubUrl: 'http://hub.test',
      createIdempotencyKey: 'create-key',
      displayMode: 'live',
      request: {
        source: 'system_audio',
        profile_id: 'default',
        segment_duration_seconds: 120,
        max_duration_seconds: 28_800,
        overlap_milliseconds: 1_000,
      },
      nextSequence: 4,
      timelineMilliseconds: 477_000,
      updatedAt: 123,
    });

    expect(store.load()).toEqual(expect.objectContaining({
      runId: 'run-a', nextSequence: 4, displayMode: 'live',
    }));
    const persisted = JSON.parse(localStorage.getItem('ananta.voice.long_run.recovery.v1') || '{}');
    expect(persisted).not.toHaveProperty('audio');
    expect(persisted).not.toHaveProperty('payload');
    expect(persisted).not.toHaveProperty('chunks');
    expect(persisted).not.toHaveProperty('plaintext');
    expect(persisted).not.toHaveProperty('previewText');
    expect(persisted).not.toHaveProperty('previewSessionId');

    store.clear('run-a');
    expect(store.load()).toBeNull();
  });

  it('retains a pending create key when the Hub response was lost before run discovery', () => {
    const store = new LocalVoiceLongRunRecoveryStore();
    store.save({
      schemaVersion: 1,
      runId: '',
      hubUrl: 'http://hub.test',
      createIdempotencyKey: 'stable-create-key',
      request: {
        source: 'microphone',
        profile_id: 'default',
        segment_duration_seconds: 120,
        max_duration_seconds: 28_800,
        overlap_milliseconds: 1_000,
      },
      nextSequence: 0,
      timelineMilliseconds: 0,
      updatedAt: 123,
    });

    expect(store.load()).toEqual(expect.objectContaining({
      runId: '', createIdempotencyKey: 'stable-create-key', displayMode: 'segment',
    }));
  });

  it('loads legacy and unknown display modes with the privacy-preserving segment default', () => {
    const metadata = {
      schemaVersion: 1,
      runId: 'run-a',
      hubUrl: 'http://hub.test',
      createIdempotencyKey: 'create-key',
      request: {
        source: 'microphone',
        profile_id: 'default',
        segment_duration_seconds: 120,
        max_duration_seconds: 28_800,
        overlap_milliseconds: 1_000,
      },
      nextSequence: 0,
      timelineMilliseconds: 0,
      updatedAt: 123,
    };
    localStorage.setItem('ananta.voice.long_run.recovery.v1', JSON.stringify(metadata));
    expect(new LocalVoiceLongRunRecoveryStore().load()?.displayMode).toBe('segment');

    localStorage.setItem('ananta.voice.long_run.recovery.v1', JSON.stringify({
      ...metadata,
      displayMode: 'unexpected',
    }));
    expect(new LocalVoiceLongRunRecoveryStore().load()?.displayMode).toBe('segment');
  });
});
