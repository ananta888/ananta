
export type VisualDeliveryMode = 'semantic' | 'ordinary';

export interface SemanticReentryEvidence {
  readonly currentReference: boolean;
  readonly contractValid: boolean;
  readonly leaseValid: boolean;
  readonly qualityGatePassed: boolean;
}

export class SemanticVisualFallbackService {
  private modeValue: VisualDeliveryMode = 'ordinary';
  private ordinaryOverride = false;
  private lastSwitchMs = Number.NEGATIVE_INFINITY;
  private stableSamples = 0;

  constructor(private readonly cooldownMs = 5_000, private readonly requiredStableSamples = 3) {}

  get mode(): VisualDeliveryMode { return this.modeValue; }
  get userForcedOrdinary(): boolean { return this.ordinaryOverride; }

  forceOrdinary(nowMs: number, userOverride = false): void {
    if (userOverride) this.ordinaryOverride = true;
    if (this.modeValue !== 'ordinary') this.lastSwitchMs = nowMs;
    this.modeValue = 'ordinary';
    this.stableSamples = 0;
  }

  clearUserOverride(): void { this.ordinaryOverride = false; this.stableSamples = 0; }

  tryEnterSemantic(evidence: Readonly<SemanticReentryEvidence>, nowMs: number): boolean {
    if (this.ordinaryOverride || nowMs - this.lastSwitchMs < this.cooldownMs
        || !evidence.currentReference || !evidence.contractValid || !evidence.leaseValid || !evidence.qualityGatePassed) {
      this.stableSamples = 0;
      return false;
    }
    this.stableSamples += 1;
    if (this.stableSamples < this.requiredStableSamples) return false;
    this.modeValue = 'semantic';
    this.lastSwitchMs = nowMs;
    this.stableSamples = 0;
    return true;
  }
}
