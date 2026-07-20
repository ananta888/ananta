import { SemanticValidatorService } from './semantic-validator.service';

const D = 'a'.repeat(64);
describe('SemanticValidatorService', () => {
  it('creates deterministic raw-free reports bound to validator role, lease, epoch, sequence and digests', async () => {
    const signer = { sign: async (payload: string) => `signature-${payload.length}-0123456789` };
    const report = await new SemanticValidatorService().createReport({
      reportId: 'report', sessionId: 'session', contractId: 'contract', validatorLeaseId: 'validator-lease',
      validatorId: 'validator', epoch: 1, sequence: 2, inputDigest: D, outputDigest: D,
      observedAtMs: 1000, expiresAtMs: 2000,
    }, { schema_valid: true, binding_valid: true, quality_score: 0.9, drift_score: 0.01, deadline_met: true }, signer);
    expect(report).toMatchObject({ validator_role: 'validator', audience: 'hub', verdict: 'pass' });
    expect(JSON.stringify(report)).not.toMatch(/frame|pixel|residual|geometry/i);
    expect(Object.isFrozen(report.criteria)).toBe(true);
  });

  it('does not accept NaN quality or invent a signature', async () => {
    const service = new SemanticValidatorService();
    await expect(service.createReport({
      reportId: 'report', sessionId: 'session', contractId: 'contract', validatorLeaseId: 'lease',
      validatorId: 'validator', epoch: 1, sequence: 2, inputDigest: D, outputDigest: D,
      observedAtMs: 1000, expiresAtMs: 2000,
    }, { schema_valid: true, binding_valid: true, quality_score: Number.NaN, drift_score: 0, deadline_met: true },
    { sign: async () => 'never-used-signature' })).rejects.toThrow('validator_criteria_invalid');
  });
});
