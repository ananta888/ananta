import {
  AcceptedPeerFailoverCommand,
  PeerOverlayParentFailover,
  PeerOverlayParentPathFactory,
  PeerOverlayParentPathPort,
} from './peer-overlay-parent-failover';

describe('PeerOverlayParentFailover', () => {
  it('keeps backup control-only and atomically fences a Hub-commanded switch', async () => {
    const factory = new FakeFactory();
    const coordinator = new PeerOverlayParentFailover(factory, () => 1_000);
    await coordinator.initialize('primary', 'backup', 4);
    expect(factory.paths.get('backup')!.setBulkEnabled).toHaveBeenLastCalledWith(false);
    await coordinator.apply(command());
    expect(factory.paths.get('primary')!.setBulkEnabled).toHaveBeenLastCalledWith(false);
    expect(factory.paths.get('primary')!.close).toHaveBeenCalledOnce();
    expect(factory.paths.get('backup')!.setBulkEnabled).toHaveBeenLastCalledWith(true);
    expect(coordinator.snapshot()).toEqual({
      primaryPeerId: 'backup', backupPeerId: null, routeEpoch: 5, permanentDoubleTrafficAllowed: false,
    });
    expect(coordinator.acceptFrame('frame-1')).toBe(true);
    expect(coordinator.acceptFrame('frame-1')).toBe(false);
  });

  it('rejects stale epochs and restores primary bulk when backup activation fails', async () => {
    const factory = new FakeFactory();
    const coordinator = new PeerOverlayParentFailover(factory, () => 1_000);
    await coordinator.initialize('primary', 'backup', 4);
    await expect(coordinator.apply(command({ routeEpoch: 4 }))).rejects.toThrow('peer_failover_command_invalid');
    factory.paths.get('backup')!.setBulkEnabled.mockRejectedValueOnce(new Error('activation_failed'));
    await expect(coordinator.apply(command())).rejects.toThrow('activation_failed');
    expect(factory.paths.get('primary')!.setBulkEnabled).toHaveBeenLastCalledWith(true);
  });

  it('rolls back when the bounded switch deadline is exceeded', async () => {
    let now = 1_000;
    const factory = new FakeFactory();
    const coordinator = new PeerOverlayParentFailover(factory, () => now, 3_000);
    await coordinator.initialize('primary', 'backup', 4);
    factory.paths.get('backup')!.setBulkEnabled.mockImplementationOnce(async () => { now = 4_001; });
    await expect(coordinator.apply(command({ expiresAtMs: 5_000 })))
      .rejects.toThrow('peer_failover_deadline_exceeded');
    expect(factory.paths.get('primary')!.setBulkEnabled).toHaveBeenLastCalledWith(true);
  });
});

class FakeFactory implements PeerOverlayParentPathFactory {
  readonly paths = new Map<string, ReturnType<typeof path>>();
  create(peerId: string): PeerOverlayParentPathPort {
    const result = path(peerId);
    this.paths.set(peerId, result);
    return result;
  }
}

function path(peerId: string) {
  return {
    peerId,
    connectControl: vi.fn(async () => undefined),
    setBulkEnabled: vi.fn(async (_enabled: boolean) => undefined),
    close: vi.fn(),
  };
}

function command(changes: Partial<AcceptedPeerFailoverCommand> = {}): AcceptedPeerFailoverCommand {
  return Object.freeze({
    validation: 'hub-failover-command-accepted-v1', commandId: 'command-1',
    publicationId: 'publication-1', primaryPeerId: 'primary', backupPeerId: 'backup',
    previousRouteEpoch: 4, routeEpoch: 5, expiresAtMs: 2_000, ...changes,
  });
}
