import { ControlCenterSecurityInspectorComponent } from './control-center-security-inspector.component';

describe('ControlCenterSecurityInspectorComponent', () => {
  it('flags secret paths and cloud warning', () => {
    const cmp = new ControlCenterSecurityInspectorComponent();
    cmp.policy = {
      riskLevel: 'high',
      allowedTools: [],
      deniedTools: [],
      allowedPaths: ['/app', '/.env'],
      deniedPaths: ['/secrets/**'],
      requiresHumanApproval: false,
      approvalReason: null,
      policyVersion: 'v1',
    } as any;

    expect(cmp.isSensitivePath('/.env')).toBeTrue();
    expect(cmp.cloudBoundaryWarning()).toBeTrue();
  });
});
