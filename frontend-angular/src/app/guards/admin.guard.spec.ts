import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { ActivatedRouteSnapshot, RouterStateSnapshot } from '@angular/router';
import { adminGuard } from './admin.guard';
import { PermissionService } from '../services/permission.service';

function runGuard(isAdmin: boolean) {
  const router = jasmine.createSpyObj<Router>('Router', ['parseUrl']);
  router.parseUrl.and.returnValue({ toString: () => '/workspace' } as any);

  TestBed.configureTestingModule({
    providers: [
      { provide: Router, useValue: router },
      { provide: PermissionService, useValue: { isAdmin: () => isAdmin } },
    ],
  });

  return TestBed.runInInjectionContext(() =>
    adminGuard({} as ActivatedRouteSnapshot, {} as RouterStateSnapshot)
  );
}

describe('adminGuard', () => {
  it('allows access for admin users', () => {
    expect(runGuard(true)).toBeTrue();
  });

  it('redirects non-admin to /workspace', () => {
    const result = runGuard(false);
    expect(result).not.toBeTrue();
  });
});
