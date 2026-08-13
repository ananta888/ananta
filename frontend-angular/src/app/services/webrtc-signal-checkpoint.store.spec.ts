import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { WebrtcSignalCheckpointStore } from './webrtc-signal-checkpoint.store';

describe('WebrtcSignalCheckpointStore epoch scoping', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it('isolates cursors and seen ids between public security epochs', () => {
    const store = new WebrtcSignalCheckpointStore();
    const base = { sessionId: 'session-a', localPeerId: 'alice', remotePeerId: 'bob' };

    store.save({ ...base, securityEpoch: 2 }, { cursor: '4', seenSignalIds: ['signal-4'] });

    expect(store.load({ ...base, securityEpoch: 2 })).toEqual({
      cursor: '4', seenSignalIds: ['signal-4'],
    });
    expect(store.load({ ...base, securityEpoch: 3 })).toEqual({
      cursor: '', seenSignalIds: [],
    });
    expect(store.load(base)).toEqual({ cursor: '', seenSignalIds: [] });
  });

  it('clears every epoch only when the owning session retires', () => {
    const store = new WebrtcSignalCheckpointStore();
    const peers = { localPeerId: 'alice', remotePeerId: 'bob' };
    store.save(
      { sessionId: 'session-a', ...peers, securityEpoch: 2 },
      { cursor: '2', seenSignalIds: ['signal-2'] },
    );
    store.save(
      { sessionId: 'session-a', ...peers, securityEpoch: 3 },
      { cursor: '3', seenSignalIds: ['signal-3'] },
    );
    store.save(
      { sessionId: 'session-b', ...peers, securityEpoch: 2 },
      { cursor: '7', seenSignalIds: ['signal-7'] },
    );

    store.clearSession('session-a');

    expect(store.load({ sessionId: 'session-a', ...peers, securityEpoch: 2 }).cursor).toBe('');
    expect(store.load({ sessionId: 'session-a', ...peers, securityEpoch: 3 }).cursor).toBe('');
    expect(store.load({ sessionId: 'session-b', ...peers, securityEpoch: 2 }).cursor).toBe('7');
  });
});
