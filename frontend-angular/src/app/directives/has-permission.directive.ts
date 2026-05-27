import { Directive, Input, TemplateRef, ViewContainerRef, inject, OnInit } from '@angular/core';
import { ActionClass } from '../services/permission-matrix';
import { PermissionService } from '../services/permission.service';

/**
 * Structural directive that removes host element from DOM when the current user
 * lacks the required action permission.
 *
 * Usage: *hasPermission="'admin_users'"
 */
@Directive({
  standalone: true,
  selector: '[hasPermission]',
})
export class HasPermissionDirective implements OnInit {
  private templateRef = inject(TemplateRef<unknown>);
  private vcr = inject(ViewContainerRef);
  private perm = inject(PermissionService);

  @Input() hasPermission!: ActionClass;

  ngOnInit() {
    if (this.perm.can(this.hasPermission)) {
      this.vcr.createEmbeddedView(this.templateRef);
    } else {
      this.vcr.clear();
    }
  }
}
