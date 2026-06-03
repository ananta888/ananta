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

    it('isAdmin returns true', () => expect(svc.isAdmin()).toBe(true));

    it('can perform all action classes', () => {
      expect(svc.can('admin_users')).toBe(true);
      expect(svc.can('admin_policies')).toBe(true);
      expect(svc.can('terminal_access')).toBe(true);
      expect(svc.can('write_approvals')).toBe(true);
      expect(svc.can('audit_read')).toBe(true);
      expect(svc.can('diagnostics_read')).toBe(true);
      expect(svc.can('manage_templates')).toBe(true);
      expect(svc.can('view_any')).toBe(true);
      expect(svc.can('view_own')).toBe(true);
      expect(svc.can('operate_tasks')).toBe(true);
    });

    it('canAll returns true for multiple permissions', () => {
      expect(svc.canAll('admin_users', 'audit_read')).toBe(true);
    });
  });

  describe('user role', () => {
    let svc: PermissionService;
    beforeEach(() => { svc = createService('user'); });

    it('isAdmin returns false', () => expect(svc.isAdmin()).toBe(false));

    it('can operate tasks and view own', () => {
      expect(svc.can('view_own')).toBe(true);
      expect(svc.can('operate_tasks')).toBe(true);
    });

    it('cannot perform sensitive actions', () => {
      expect(svc.can('admin_users')).toBe(false);
      expect(svc.can('admin_policies')).toBe(false);
      expect(svc.can('terminal_access')).toBe(false);
      expect(svc.can('write_approvals')).toBe(false);
      expect(svc.can('audit_read')).toBe(false);
      expect(svc.can('diagnostics_read')).toBe(false);
      expect(svc.can('manage_templates')).toBe(false);
      expect(svc.can('view_any')).toBe(false);
    });

    it('canAll returns false when any permission missing', () => {
      expect(svc.canAll('view_own', 'admin_users')).toBe(false);
    });
  });

  describe('no token / unknown role', () => {
    it('defaults to user-level permissions', () => {
      const svc = createService(null);
      expect(svc.isAdmin()).toBe(false);
      expect(svc.can('admin_users')).toBe(false);
      expect(svc.can('operate_tasks')).toBe(true);
    });
  });
});
