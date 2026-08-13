import { ActivatedRouteSnapshot, RouterStateSnapshot } from '@angular/router';
import { describe, expect, it, vi } from 'vitest';

import {
  CaseFlowStudioDeactivationAware,
  caseFlowStudioDirtyGuard,
} from './caseflow-studio.guard';

describe('caseFlowStudioDirtyGuard', () => {
  it.each([true, false])('returns the component decision without a save side effect (%s)', decision => {
    const canLeaveCaseFlowStudio = vi.fn(() => decision);
    const component: CaseFlowStudioDeactivationAware = { canLeaveCaseFlowStudio };

    const result = caseFlowStudioDirtyGuard(
      component,
      {} as ActivatedRouteSnapshot,
      {} as RouterStateSnapshot,
      { url: '/workspace' } as RouterStateSnapshot,
    );

    expect(result).toBe(decision);
    expect(canLeaveCaseFlowStudio).toHaveBeenCalledOnce();
  });
});
