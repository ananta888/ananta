import { TestBed } from '@angular/core/testing';
import { PermissionService } from './permission.service';
import { UserAuthService } from './user-auth.service';

function mockAuth(role: string | null) {
  return {
    userPayload: role ? { role } : null,
    token: role ? 'dummy-token' : null,
  } as unknown as UserAuthService;
}

describe('PermissionService', () => {
  function createService(role: string | null) {
    TestBed.configureTestingModule({
      providers: [
        PermissionService,
        { provide: UserAuthService, useValue: mockAuth(role) },
      ],
    });
    return TestBed.inject(PermissionService);
  }

  describe('admin role', () => {
    let svc: PermissionService;
    beforeEach(() => { svc = createService('admin'); });

    it('isAdmin returns true', () => expect(svc.isAdmin()).toBeTrue());

    it('can perform all action classes', () => {
      expect(svc.can('admin_users')).toBeTrue();
      expect(svc.can('admin_policies')).toBeTrue();
      expect(svc.can('terminal_access')).toBeTrue();
      expect(svc.can('write_approvals')).toBeTrue();
      expect(svc.can('audit_read')).toBeTrue();
      expect(svc.can('diagnostics_read')).toBeTrue();
      expect(svc.can('manage_templates')).toBeTrue();
      expect(svc.can('view_any')).toBeTrue();
      expect(svc.can('view_own')).toBeTrue();
      expect(svc.can('operate_tasks')).toBeTrue();
    });

    it('canAll returns true for multiple permissions', () => {
      expect(svc.canAll('admin_users', 'audit_read')).toBeTrue();
    });
  });

  describe('user role', () => {
    let svc: PermissionService;
    beforeEach(() => { svc = createService('user'); });

    it('isAdmin returns false', () => expect(svc.isAdmin()).toBeFalse());

    it('can operate tasks and view own', () => {
      expect(svc.can('view_own')).toBeTrue();
      expect(svc.can('operate_tasks')).toBeTrue();
    });

    it('cannot perform sensitive actions', () => {
      expect(svc.can('admin_users')).toBeFalse();
      expect(svc.can('admin_policies')).toBeFalse();
      expect(svc.can('terminal_access')).toBeFalse();
      expect(svc.can('write_approvals')).toBeFalse();
      expect(svc.can('audit_read')).toBeFalse();
      expect(svc.can('diagnostics_read')).toBeFalse();
      expect(svc.can('manage_templates')).toBeFalse();
      expect(svc.can('view_any')).toBeFalse();
    });

    it('canAll returns false when any permission missing', () => {
      expect(svc.canAll('view_own', 'admin_users')).toBeFalse();
    });
  });

  describe('no token / unknown role', () => {
    it('defaults to user-level permissions', () => {
      const svc = createService(null);
      expect(svc.isAdmin()).toBeFalse();
      expect(svc.can('admin_users')).toBeFalse();
      expect(svc.can('operate_tasks')).toBeTrue();
    });
  });
});
