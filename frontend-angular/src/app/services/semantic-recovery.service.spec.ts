import { SemanticRecoveryService } from './semantic-recovery.service';
import { SemanticVisualFallbackService } from './semantic-visual-fallback.service';

describe('SemanticRecoveryService', () => {
  it('escalates bounded region repair, reference refresh and ordinary fallback independently by trigger', () => {
    const fallback = new SemanticVisualFallbackService(100, 2);
    const recovery = new SemanticRecoveryService(fallback, 10, 1000);
    expect(recovery.recover('receiver', 'chunk_loss', 0).action).toBe('region_repair');
    expect(recovery.recover('receiver', 'chunk_loss', 11).action).toBe('region_repair');
    expect(recovery.recover('receiver', 'chunk_loss', 22).action).toBe('reference_refresh');
    expect(recovery.recover('receiver', 'chunk_loss', 33).action).toBe('ordinary_fallback');
    expect(fallback.mode).toBe('ordinary');
    expect(recovery.recover('other', 'scene_cut', 0).action).toBe('reference_refresh');
    expect(recovery.recover('lease', 'lease_loss', 0).action).toBe('ordinary_fallback');
  });

  it('uses cooldown to prevent oscillation without consuming the attempt budget', () => {
    const recovery = new SemanticRecoveryService(undefined, 100, 1000);
    expect(recovery.recover('receiver', 'drift', 0).attempt).toBe(1);
    expect(recovery.recover('receiver', 'drift', 50)).toMatchObject({ action: 'cooldown_hold', attempt: 1 });
    expect(recovery.recover('receiver', 'drift', 101)).toMatchObject({ action: 'reference_refresh', attempt: 2 });
  });

  it('makes user ordinary override absolute and requires all re-entry evidence plus hysteresis', () => {
    const fallback = new SemanticVisualFallbackService(100, 2);
    fallback.forceOrdinary(0, true);
    const good = { currentReference: true, contractValid: true, leaseValid: true, qualityGatePassed: true };
    expect(fallback.tryEnterSemantic(good, 1000)).toBe(false);
    fallback.clearUserOverride();
    expect(fallback.tryEnterSemantic({ ...good, leaseValid: false }, 1000)).toBe(false);
    expect(fallback.tryEnterSemantic(good, 1100)).toBe(false);
    expect(fallback.tryEnterSemantic(good, 1200)).toBe(true);
    expect(fallback.mode).toBe('semantic');
  });
});
