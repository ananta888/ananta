import { Component, ChangeDetectionStrategy } from '@angular/core';
import { RefactoringPanelComponent } from './refactoring-panel.component';
import { CustomAgentEditorComponent } from './custom-agent-editor.component';

/**
 * Agenten-View — kombiniert CH-005 (Refactoring) und CH-006 (Custom Agents).
 *
 * Beide Bereiche in einer Page, getrennt durch Ueberschriften.
 * Refactoring ist write-mode-gated (PolicyService).
 * Custom-Agent-Editor ebenfalls write-mode-gated.
 */
@Component({
  selector: 'ch-agents',
  standalone: true,
  imports: [RefactoringPanelComponent, CustomAgentEditorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-agents-page">
      <header class="ch-page-head">
        <h2>Agenten</h2>
        <p class="ch-muted">Refactoring und Custom-Agent-Konfiguration.</p>
      </header>
      <article class="ch-agent-section">
        <h3>Refactoring</h3>
        <p class="ch-muted">Vorschlaege, Diff-Vorschau, Apply. Apply erfordert Write-Modus.</p>
        <ch-refactoring-panel />
      </article>
      <article class="ch-agent-section">
        <h3>Custom Agents</h3>
        <p class="ch-muted">Eigene Agent-Profile anlegen, bearbeiten, ausfuehren.</p>
        <ch-custom-agent-editor />
      </article>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 14px; }
    .ch-agents-page { display: grid; gap: 18px; max-width: 1200px; }
    .ch-page-head h2 { margin: 0 0 4px; font-size: 20px; }
    .ch-page-head { margin-bottom: 4px; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 0 0 10px; }
    .ch-agent-section {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--card-bg);
      padding: 12px 14px;
    }
    .ch-agent-section h3 { margin: 0 0 6px; font-size: 14px; }
  `]
})
export class CodeHugAgentsComponent {}