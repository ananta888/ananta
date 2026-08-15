import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges, computed, inject, signal } from '@angular/core';
import {
  type TaskView,
  type TraceEntry,
  type WorkBucket,
  type WorkScope,
  bucketCounts,
  bucketLabel,
  byAssignee,
  inBucket,
  scopeIsAnswerable,
} from './caseflow-work.models';
import { CaseFlowWorkService } from './caseflow-work.service';

type Panel = 'who' | 'emerging' | 'trace';

/**
 * What is happening at one level, whichever level that is.
 *
 * The same three questions are worth asking of an organisation, of a team and
 * of a single agent — who is busy, what is appearing, and what happened — so
 * the same panel answers them at every level and only its scope changes. That
 * is what makes clicking down through a structure feel like one view rather
 * than three.
 */
@Component({
  selector: 'app-caseflow-work-panel',
  standalone: true,
  imports: [CommonModule],
  styleUrl: './caseflow-work-panel.component.scss',
  template: `
    <section class="work">
      <nav class="work-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="panel() === 'who'"
          [class.work-tab--active]="panel() === 'who'"
          (click)="panel.set('who')"
        >
          Arbeitet gerade ({{ counts().working }})
        </button>
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="panel() === 'emerging'"
          [class.work-tab--active]="panel() === 'emerging'"
          (click)="panel.set('emerging')"
        >
          Entsteht gerade ({{ counts().emerging }})
        </button>
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="panel() === 'trace'"
          [class.work-tab--active]="panel() === 'trace'"
          (click)="panel.set('trace')"
        >
          Verlauf ({{ trace().length }})
        </button>
        <button type="button" class="work-ghost" (click)="reload()" [disabled]="loading()">
          {{ loading() ? 'Lädt …' : 'Aktualisieren' }}
        </button>
      </nav>

      @if (!answerable()) {
        <p class="work-muted">
          Diese Ebene trägt keine eigene Kennung, nach der sich Aufgaben filtern lassen. Es wird
          deshalb nichts angezeigt — lieber nichts als die Arbeit aller unter einem Namen.
        </p>
      } @else if (error(); as message) {
        <p class="work-error" role="alert">{{ message }}</p>
      } @else {
        @switch (panel()) {
          @case ('who') {
            @if (!assignees().length) {
              <p class="work-muted">Hier arbeitet gerade niemand.</p>
            } @else {
              <ul class="work-assignees">
                @for (row of assignees(); track row.agent) {
                  <li class="work-assignee">
                    <span class="work-agent">
                      <span aria-hidden="true">🤖</span>
                      {{ row.agent }}
                      <span class="work-badge">{{ row.working.length }} aktiv</span>
                    </span>
                    <ul class="work-tasks">
                      @for (task of row.working; track task.id) {
                        <li class="work-task">
                          <span class="work-task-title">{{ task.title }}</span>
                          <span class="work-status work-status--working">{{ task.status }}</span>
                        </li>
                      }
                      @for (task of row.other; track task.id) {
                        <li class="work-task work-task--quiet">
                          <span class="work-task-title">{{ task.title }}</span>
                          <span class="work-status">{{ task.status }}</span>
                        </li>
                      }
                    </ul>
                  </li>
                }
              </ul>
            }
          }

          @case ('emerging') {
            @if (!emerging().length) {
              <p class="work-muted">Gerade entsteht nichts Neues.</p>
            } @else {
              <ul class="work-tasks work-tasks--flat">
                @for (task of emerging(); track task.id) {
                  <li class="work-task">
                    <span aria-hidden="true">✨</span>
                    <span class="work-task-title">{{ task.title }}</span>
                    <span class="work-status">{{ task.status }}</span>
                    @if (task.agent) {
                      <span class="work-status">{{ task.agent }}</span>
                    }
                  </li>
                }
              </ul>
            }
            @if (waiting().length) {
              <h4 class="work-heading">{{ waitingLabel }} ({{ waiting().length }})</h4>
              <ul class="work-tasks work-tasks--flat">
                @for (task of waiting(); track task.id) {
                  <li class="work-task work-task--quiet">
                    <span aria-hidden="true">⏸️</span>
                    <span class="work-task-title">{{ task.title }}</span>
                    <span class="work-status">{{ task.status }}</span>
                  </li>
                }
              </ul>
            }
          }

          @case ('trace') {
            @if (!trace().length) {
              <p class="work-muted">Für diese Ebene ist noch nichts protokolliert.</p>
            } @else {
              <ol class="work-trace">
                @for (entry of trace(); track entry.key) {
                  <li class="work-trace-line" [class.work-trace-line--creating]="entry.creating">
                    <span class="work-trace-actor">{{ entry.actor }}</span>
                    <span class="work-trace-summary">{{ entry.summary }}</span>
                    <span class="work-trace-type">{{ entry.event_type }}</span>
                  </li>
                }
              </ol>
            }
          }
        }
      }
    </section>
  `,
})
export class CaseFlowWorkPanelComponent implements OnChanges {
  private readonly work = inject(CaseFlowWorkService);

  @Input({ required: true }) scope!: WorkScope;

  protected readonly waitingLabel = bucketLabel('waiting');

  protected readonly panel = signal<Panel>('who');
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly trace = signal<readonly TraceEntry[]>([]);

  private readonly tasks = signal<readonly TaskView[]>([]);

  protected readonly counts = computed(() => bucketCounts(this.tasks()));
  protected readonly assignees = computed(() => byAssignee(this.tasks()));
  protected readonly emerging = computed(() => this.bucket('emerging'));
  protected readonly waiting = computed(() => this.bucket('waiting'));

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['scope']) this.reload();
  }

  protected answerable(): boolean {
    return Boolean(this.scope) && scopeIsAnswerable(this.scope);
  }

  protected reload(): void {
    if (!this.answerable()) {
      this.tasks.set([]);
      this.trace.set([]);
      return;
    }
    const scope = this.scope;
    this.loading.set(true);
    this.error.set(null);
    this.work.tasks(scope).subscribe({
      next: tasks => {
        this.loading.set(false);
        if (this.scope === scope) this.tasks.set(tasks);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Die Aufgaben dieser Ebene konnten nicht gelesen werden.');
      },
    });
    // The trace is its own read: a missing timeline must not hide the tasks.
    this.work.trace(scope).subscribe({
      next: entries => {
        if (this.scope === scope) this.trace.set(entries);
      },
      error: () => this.trace.set([]),
    });
  }

  private bucket(name: WorkBucket): readonly TaskView[] {
    return inBucket(this.tasks(), name);
  }
}
