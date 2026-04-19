import { Component, EventEmitter, Input, Output } from '@angular/core';
import { EmptyStateComponent } from '../state/empty-state.component';
import { LoadingStateComponent } from '../state/loading-state.component';

@Component({
  standalone: true,
  selector: 'app-table-shell',
  imports: [EmptyStateComponent, LoadingStateComponent],
  template: `
    <section class="shared-table-shell" [attr.aria-label]="ariaLabel || title">
      @if (title || subtitle || refreshLabel) {
        <div class="row space-between align-start shared-table-header">
          <div>
            @if (title) {
              <h4 class="no-margin">{{ title }}</h4>
            }
            @if (subtitle) {
              <p class="muted mt-sm no-margin">{{ subtitle }}</p>
            }
          </div>
          <div class="shared-table-toolbar">
            <ng-content select="[table-toolbar]"></ng-content>
            @if (refreshLabel) {
              <button class="secondary btn-small" type="button" (click)="refresh.emit()" [disabled]="loading">{{ refreshLabel }}</button>
            }
          </div>
        </div>
      }

      @if (loading) {
        <app-loading-state [label]="loadingLabel" [count]="loadingRows" [lineCount]="2"></app-loading-state>
      } @else if (empty) {
        <app-empty-state
          [title]="emptyTitle"
          [description]="emptyDescription"
          [compact]="true"
        ></app-empty-state>
      } @else {
        <div class="table-scroll mt-sm">
          <ng-content></ng-content>
        </div>
      }
    </section>
  `,
})
export class TableShellComponent {
  @Input() title = '';
  @Input() subtitle = '';
  @Input() ariaLabel = '';
  @Input() loading = false;
  @Input() empty = false;
  @Input() loadingLabel = 'Tabelle wird geladen';
  @Input() loadingRows = 3;
  @Input() emptyTitle = 'Keine Daten vorhanden';
  @Input() emptyDescription = '';
  @Input() refreshLabel = '';
  @Output() refresh = new EventEmitter<void>();
}
