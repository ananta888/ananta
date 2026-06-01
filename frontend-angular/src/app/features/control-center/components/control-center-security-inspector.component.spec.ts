import { ControlCenterSecurityInspectorComponent } from './control-center-security-inspector.component';

describe('ControlCenterSecurityInspectorComponent', () => {
  it('shows cloud warning only when sensitive paths are allowed and cloud is allowed', () => {
    const cmp = new ControlCenterSecurityInspectorComponent();
    cmp.policy = {
      riskLevel: 'high',
      allowedTools: [],
      deniedTools: [],
      allowedPaths: ['/app', '/.env'],
      deniedPaths: ['/secrets/**'],
      cloudAllowed: true,
      runtimeBoundary: 'cloud-allowed',
      requiresHumanApproval: false,
      approvalReason: null,
      policyVersion: 'v1',
    } as any;

    expect(cmp.isSensitivePath('/.env')).toBeTruthy();
    expect(cmp.cloudBoundaryWarning()).toBeTruthy();
  });

  it('does not show cloud warning when sensitive paths are denied only', () => {
    const cmp = new ControlCenterSecurityInspectorComponent();
    cmp.policy = {
      riskLevel: 'medium',
      allowedTools: [],
      deniedTools: [],
      allowedPaths: ['/app/src'],
      deniedPaths: ['/.env', '/secrets/**'],
      cloudAllowed: true,
      runtimeBoundary: 'cloud-allowed',
      requiresHumanApproval: false,
      approvalReason: null,
      policyVersion: 'v1',
    } as any;

    expect(cmp.cloudBoundaryWarning()).toBeFalsy();
  });
});
