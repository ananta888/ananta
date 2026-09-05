import {
  ActiveMediaTopology,
  MediaTopologyTransitionCoordinator,
  MediaTopologyTransitionPort,
  StagedMediaTopology,
} from './media-topology-transition-coordinator';

describe('MediaTopologyTransitionCoordinator', () => {
  it('stages and atomically swaps one fenced bulk path', async () => {
    const port = new FakeTransitionPort();
    const coordinator = new MediaTopologyTransitionCoordinator(port);
    const first = await coordinator.apply({
      target: 'ordinary_mesh', reasonCode: 'mesh_ready', changed: true,
    });
    const second = await coordinator.apply({
      target: 'ordinary_sfu', reasonCode: 'mesh_capacity_exceeded', changed: true,
    });
    expect(first.state).toBe('activated');
    expect(second.state).toBe('activated');
    expect(coordinator.snapshot()?.target).toBe('ordinary_sfu');
    expect(port.maximumActivePaths).toBe(1);
  });

  it('rolls back a failed target and retains the previous active path', async () => {
    const port = new FakeTransitionPort();
    const coordinator = new MediaTopologyTransitionCoordinator(port);
    await coordinator.apply({ target: 'ordinary_mesh', reasonCode: 'mesh_ready', changed: true });
    port.failTarget = 'ordinary_sfu';
    const failed = await coordinator.apply({
      target: 'ordinary_sfu', reasonCode: 'prefer_sfu', changed: true,
    });
    expect(failed.state).toBe('rolled_back');
    expect(coordinator.snapshot()?.target).toBe('ordinary_mesh');
    expect(port.rolledBack).toContain('ordinary_sfu');
  });

  it('rolls back a staged transition superseded during asynchronous preparation', async () => {
    const port = new FakeTransitionPort();
    port.stageGate = deferred<void>();
    const coordinator = new MediaTopologyTransitionCoordinator(port);
    const pending = coordinator.apply({
      target: 'ordinary_mesh', reasonCode: 'mesh_ready', changed: true,
    });
    coordinator.supersede();
    port.stageGate.resolve();
    expect((await pending).state).toBe('superseded');
    expect(port.rolledBack).toContain('ordinary_mesh');
  });
});

class FakeTransitionPort implements MediaTopologyTransitionPort {
  readonly rolledBack: string[] = [];
  failTarget: string | null = null;
  stageGate: Deferred<void> | null = null;
  maximumActivePaths = 0;
  private activePaths = 0;

  async stage(target: string, fence: number): Promise<StagedMediaTopology> {
    await this.stageGate?.promise;
    return { target, fence };
  }

  async swap(_current: ActiveMediaTopology | null, staged: StagedMediaTopology): Promise<ActiveMediaTopology> {
    if (staged.target === this.failTarget) throw new Error('media_topology_target_failed');
    this.activePaths = 1;
    this.maximumActivePaths = Math.max(this.maximumActivePaths, this.activePaths);
    return { target: staged.target, fence: staged.fence };
  }

  async rollback(staged: StagedMediaTopology): Promise<void> {
    this.rolledBack.push(staged.target);
  }
}

interface Deferred<T> {
  readonly promise: Promise<T>;
  resolve(value: T): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  return { promise: new Promise<T>(done => { resolve = done; }), resolve };
}
