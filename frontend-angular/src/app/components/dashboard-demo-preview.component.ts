import { Component, EventEmitter, Input, Output } from '@angular/core';

import { ErrorStateComponent } from '../shared/ui/state';
import { UiSkeletonComponent } from './ui-skeleton.component';

export interface DemoPreviewExample {
  id: string;
  title: string;
  goal: string;
  outcome: string;
  tasks: string[];
  starter_context?: string;
}

@Component({
  standalone: true,
  selector: 'app-dashboard-demo-preview',
  imports: [ErrorStateComponent, UiSkeletonComponent],
  template: `
    <section class="card mb-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Demo-Vorschau</h3>
          <p class="muted font-sm mt-sm no-margin">
            Beispiele sind read-only und bleiben vom echten Arbeitsmodus getrennt.
          </p>
        </div>
        <button class="secondary btn-small" type="button" (click)="close.emit()">Schliessen</button>
      </div>
      @if (loading) {
        <app-ui-skeleton [count]="3" [columns]="3" [lineCount]="3" lineClass="skeleton line"></app-ui-skeleton>
      } @else if (error) {
        <app-error-state
          title="Demo konnte nicht geladen werden"
          [message]="error"
          retryLabel="Erneut versuchen"
          (retry)="retry.emit()"
        ></app-error-state>
      } @else if (examples.length) {
        <div class="grid cols-3 mt-sm">
          @for (example of examples; track example.id) {
            <article class="card-light demo-example">
              <h4>{{ example.title }}</h4>
              <p class="muted">{{ example.goal }}</p>
              <strong>{{ example.outcome }}</strong>
              <ul>
                @for (task of example.tasks; track task) {
                  <li>{{ task }}</li>
                }
              </ul>
              <button class="primary btn-small mt-sm" type="button" (click)="startExample.emit(example)" [disabled]="busy">
                Als Goal starten
              </button>
            </article>
          }
        </div>
      }
    </section>
  `,
})
export class DashboardDemoPreviewComponent {
  @Input() examples: DemoPreviewExample[] = [];
  @Input() loading = false;
  @Input() error = '';
  @Input() busy = false;

  @Output() close = new EventEmitter<void>();
  @Output() retry = new EventEmitter<void>();
  @Output() startExample = new EventEmitter<DemoPreviewExample>();
}
