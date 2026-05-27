import { Injectable, inject } from '@angular/core';
import { UserAuthService } from './user-auth.service';
import { ActionClass, PERMISSION_MATRIX, roleFromString } from './permission-matrix';

@Injectable({ providedIn: 'root' })
export class PermissionService {
  private auth = inject(UserAuthService);

  get currentRole() {
    return roleFromString(this.auth.userPayload?.role);
  }

  isAdmin(): boolean {
    return this.currentRole === 'admin';
  }

  can(action: ActionClass): boolean {
    return PERMISSION_MATRIX[this.currentRole].has(action);
  }

  canAll(...actions: ActionClass[]): boolean {
    return actions.every(a => this.can(a));
  }
}
