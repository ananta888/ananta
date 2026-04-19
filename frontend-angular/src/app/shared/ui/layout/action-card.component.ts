import { NgTemplateOutlet } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-action-card',
  imports: [NgTemplateOutlet, RouterLink],
  template: `
    @if (routerLink) {
      <a class="card shared-action-card" [routerLink]="routerLink" [attr.aria-label]="ariaLabel || title">
        <ng-container [ngTemplateOutlet]="content"></ng-container>
      </a>
    } @else if (href) {
      <a class="card shared-action-card" [href]="href" [attr.aria-label]="ariaLabel || title">
        <ng-container [ngTemplateOutlet]="content"></ng-container>
      </a>
    } @else {
      <button class="card shared-action-card shared-action-card-button" type="button" [disabled]="disabled" (click)="action.emit()" [attr.aria-label]="ariaLabel || title">
        <ng-container [ngTemplateOutlet]="content"></ng-container>
      </button>
    }

    <ng-template #content>
      <div class="shared-action-card-main">
        <strong>{{ title }}</strong>
        @if (description) {
          <span>{{ description }}</span>
        }
      </div>
      @if (badge) {
        <span class="badge">{{ badge }}</span>
      }
    </ng-template>
  `,
})
export class ActionCardComponent {
  @Input() title = '';
  @Input() description = '';
  @Input() badge = '';
  @Input() href = '';
  @Input() routerLink: string | unknown[] | null = null;
  @Input() ariaLabel = '';
  @Input() disabled = false;
  @Output() action = new EventEmitter<void>();
}
