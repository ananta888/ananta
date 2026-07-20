import { SemanticVisualControllerService, SemanticVisualControllerInput } from './semantic-visual-controller.service';

const input = (patch: Partial<SemanticVisualControllerInput> = {}): SemanticVisualControllerInput => ({
  receiverId: 'receiver', nowMs: 10_000, requestedMode: 'active', releaseGatePassed: true,
  userOrdinaryOverride: false,
  authority: {
    captureCapability: true, contractId: 'contract', contractActive: true, contractExpiresAtMs: 20_000,
    leaseId: 'lease', leaseActive: true, leaseExpiresAtMs: 20_000,
    qualityReportId: 'report', qualityReportPassed: true, qualityReportExpiresAtMs: 20_000,
  },
  metrics: { byteRatio: 0.4, cpuRatio: 1, workingBytes: 1000, qualityScore: 0.9, driftScore: 0.01 },
  ...patch,
});

describe('SemanticVisualControllerService', () => {
  it('cannot invent release, contract, lease or quality authority', () => {
    const service = new SemanticVisualControllerService();
    expect(service.decide(input({ releaseGatePassed: false }))).toMatchObject({ mode: 'ordinary', reasonCode: 'visual_release_gate_closed' });
    expect(service.decide(input({ authority: { ...input().authority, leaseId: null } }))).toMatchObject({
      mode: 'ordinary', reasonCode: 'visual_authority_evidence_missing',
    });
  });

  it('uses fixed thresholds and three-sample hysteresis before semantic mode', () => {
    const service = new SemanticVisualControllerService();
    expect(service.decide(input()).mode).toBe('observe_only');
    expect(service.decide(input()).mode).toBe('observe_only');
    expect(service.decide(input()).mode).toBe('semantic');
    expect(service.decide(input({ metrics: { ...input().metrics, byteRatio: 2 } }))).toMatchObject({
      mode: 'ordinary', reasonCode: 'visual_quality_or_resource_no_go', residualBudgetBytes: 0,
    });
  });

  it('always honors user Ordinary override', () => {
    expect(new SemanticVisualControllerService().decide(input({ userOrdinaryOverride: true })))
      .toMatchObject({ mode: 'ordinary', reasonCode: 'user_ordinary_override' });
  });
});
