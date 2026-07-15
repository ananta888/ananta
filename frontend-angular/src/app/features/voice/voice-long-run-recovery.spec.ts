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

    expect(store.load()).toEqual(expect.objectContaining({ runId: 'run-a', nextSequence: 4 }));
    const persisted = JSON.parse(localStorage.getItem('ananta.voice.long_run.recovery.v1') || '{}');
    expect(persisted).not.toHaveProperty('audio');
    expect(persisted).not.toHaveProperty('payload');
    expect(persisted).not.toHaveProperty('chunks');
    expect(persisted).not.toHaveProperty('plaintext');

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
      runId: '', createIdempotencyKey: 'stable-create-key',
    }));
  });
});
