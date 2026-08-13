import { CanDeactivateFn } from '@angular/router';

export interface CaseFlowStudioDeactivationAware {
  canLeaveCaseFlowStudio(): boolean;
}

/** Route boundary only; confirmation owns no persistence side effects. */
export const caseFlowStudioDirtyGuard:
CanDeactivateFn<CaseFlowStudioDeactivationAware> = component =>
  component.canLeaveCaseFlowStudio();
