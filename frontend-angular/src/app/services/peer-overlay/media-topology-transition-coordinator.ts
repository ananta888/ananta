export interface MediaTopologyDecision {
  readonly target: string;
  readonly reasonCode: string;
  readonly changed: boolean;
}

export interface StagedMediaTopology {
  readonly target: string;
  readonly fence: number;
}

export interface ActiveMediaTopology {
  readonly target: string;
  readonly fence: number;
}

/** Adapter boundary whose swap must expose at most one active bulk path. */
export interface MediaTopologyTransitionPort {
  stage(target: string, fence: number): Promise<StagedMediaTopology>;
  swap(
    current: ActiveMediaTopology | null,
    staged: StagedMediaTopology,
  ): Promise<ActiveMediaTopology>;
  rollback(staged: StagedMediaTopology): Promise<void>;
}

export interface MediaTopologyTransitionResult {
  readonly state: 'unchanged' | 'activated' | 'superseded' | 'rolled_back';
  readonly target: string;
  readonly fence: number;
  readonly reasonCode: string;
}

/** Browser-side executor; the Hub policy remains the topology authority. */
export class MediaTopologyTransitionCoordinator {
  private generation = 0;
  private active: ActiveMediaTopology | null = null;

  constructor(private readonly transitions: MediaTopologyTransitionPort) {}

  async apply(decision: Readonly<MediaTopologyDecision>): Promise<MediaTopologyTransitionResult> {
    if (!decision.changed) return result('unchanged', decision.target, this.generation, decision.reasonCode);
    const fence = ++this.generation;
    let staged: StagedMediaTopology | null = null;
    try {
      staged = await this.transitions.stage(decision.target, fence);
      if (fence !== this.generation) {
        await this.transitions.rollback(staged);
        return result('superseded', decision.target, fence, 'media_topology_transition_superseded');
      }
      this.active = await this.transitions.swap(this.active, staged);
      if (this.active.target !== decision.target || this.active.fence !== fence) {
        throw new Error('media_topology_transition_binding_invalid');
      }
      return result('activated', decision.target, fence, decision.reasonCode);
    } catch (error) {
      if (staged) await this.transitions.rollback(staged).catch(() => undefined);
      return result(
        'rolled_back',
        this.active?.target ?? decision.target,
        fence,
        error instanceof Error ? error.message : 'media_topology_transition_failed',
      );
    }
  }

  supersede(): void { this.generation += 1; }

  snapshot(): ActiveMediaTopology | null {
    return this.active ? Object.freeze({ ...this.active }) : null;
  }
}

function result(
  state: MediaTopologyTransitionResult['state'],
  target: string,
  fence: number,
  reasonCode: string,
): MediaTopologyTransitionResult {
  return Object.freeze({ state, target, fence, reasonCode });
}
