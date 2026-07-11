import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  ChatSession,
  ChatFolder,
  OrganizationOperation,
  OrganizationRevision,
  ReorganizeProposal,
} from '../services/chat-sessions.service';

@Component({
  selector: 'app-chat-organization-dialogs',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    @if (proposal) {
      <div class="modal-backdrop" (click)="cancel.emit()">
        <div class="modal" (click)="$event.stopPropagation()">
          <div class="modal-title">🤖 KI-Reorganisierungsvorschlag</div>
          <div class="modal-summary">{{ proposal.summary }}</div>
          <div class="muted">Datenmodus: {{ proposal.input_policy }} · Status: {{ proposal.status }}</div>
          @for (issue of proposal.validation_errors; track $index) {
            <div class="err">{{ issue.error_code }}: {{ issue.message }}</div>
          }
          @for (operation of proposal.operations; track operation.operation_id) {
            <div class="proposal-item">
              <strong>{{ operationLabel(operation) }}</strong>
              @if (operation.type === 'folder.create') {
                <input [ngModel]="folderName(operation)" (ngModelChange)="setFolderName(operation, $event)" />
                <input class="icon-input" [ngModel]="folderIcon(operation)"
                       (ngModelChange)="setFolderIcon(operation, $event)" maxlength="4" />
              } @else if (operation.type === 'conversation.move') {
                <span>{{ conversationName(operation.target_id) }} →</span>
                <select [ngModel]="operation.after" (ngModelChange)="setOperationTarget(operation, $event)">
                  <option value="">Root</option>
                  @for (folder of folders; track folder.id) { <option [value]="folder.id">{{ folder.name }}</option> }
                  @for (folder of proposal.folders; track folder.id) { <option [value]="folder.id">{{ folder.name }} (neu)</option> }
                </select>
              } @else {
                <span>{{ operation.before || '–' }} → {{ operation.after || '–' }}</span>
              }
              @if (operation.rationale) { <span class="muted"> · {{ operation.rationale }}</span> }
              @if (operation.type.includes('rename')) {
                <button (click)="removeOperation(operation.operation_id)">Umbenennung entfernen</button>
              }
            </div>
          }
          <div class="modal-actions">
            <button (click)="cancel.emit()">Abbrechen</button>
            <button (click)="apply.emit()" [disabled]="proposal.status !== 'ready'">Atomar übernehmen</button>
          </div>
        </div>
      </div>
    }

    @if (historyVisible) {
      <div class="modal-backdrop" (click)="closeHistory.emit()">
        <div class="modal" (click)="$event.stopPropagation()">
          <div class="modal-title">↶ Organisationsverlauf</div>
          @for (revision of history; track revision.id) {
            <div class="proposal-item">
              <strong>{{ revision.source === 'ai' ? '🤖' : '👤' }} {{ revision.summary }}</strong>
              <span class="muted">{{ formatDate(revision.created_at) }} · {{ revision.applied_operations.length }} Änderungen</span>
              <button (click)="revert.emit(revision)">Rückgängig</button>
              @for (operation of revision.applied_operations; track operation.operation_id) {
                <span class="muted">{{ operationLabel(operation) }}: {{ operation.before || '–' }} → {{ operation.after || '–' }}</span>
              }
            </div>
          } @empty {
            <div class="muted">Noch keine Organisationsrevisionen.</div>
          }
          <div class="modal-actions"><button (click)="closeHistory.emit()">Schließen</button></div>
        </div>
      </div>
    }
  `,
  styles: [`
    .modal-backdrop { position: fixed; inset: 0; z-index: 1000; background: #000a; display: grid; place-items: center; }
    .modal { width: min(720px, 92vw); max-height: 82vh; overflow: auto; padding: 18px; border: 1px solid #345; border-radius: 8px; background: #101820; color: #e8eef3; }
    .modal-title { font-weight: 700; font-size: 1.1rem; margin-bottom: 8px; }
    .modal-summary, .proposal-item { margin: 8px 0; }
    .proposal-item { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 7px; background: #17232d; border-radius: 5px; }
    .proposal-item strong { min-width: 160px; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
    .muted { color: #9babb7; font-size: .86rem; }
    .err { color: #ff9b9b; margin: 6px 0; }
  `],
})
export class ChatOrganizationDialogsComponent {
  @Input() proposal: ReorganizeProposal | null = null;
  @Input() sessions: ChatSession[] = [];
  @Input() folders: ChatFolder[] = [];
  @Input() history: OrganizationRevision[] = [];
  @Input() historyVisible = false;
  @Output() apply = new EventEmitter<void>();
  @Output() cancel = new EventEmitter<void>();
  @Output() proposalChanged = new EventEmitter<ReorganizeProposal>();
  @Output() revert = new EventEmitter<OrganizationRevision>();
  @Output() closeHistory = new EventEmitter<void>();

  operationLabel(operation: OrganizationOperation): string {
    const labels: Record<string, string> = {
      'folder.create': 'Ordner anlegen',
      'folder.rename': 'Ordner umbenennen',
      'folder.move': 'Ordner verschieben',
      'conversation.move': 'Conversation verschieben',
      'conversation.rename': 'Conversation umbenennen',
    };
    return labels[operation.type] ?? operation.type;
  }

  folderName(operation: OrganizationOperation): string {
    return String((operation.after as Record<string, unknown> | undefined)?.['name'] ?? '');
  }

  folderIcon(operation: OrganizationOperation): string {
    return String((operation.after as Record<string, unknown> | undefined)?.['icon'] ?? '📁');
  }

  setFolderName(operation: OrganizationOperation, name: string): void {
    if (!this.proposal) return;
    operation.after = { ...((operation.after as Record<string, unknown>) || {}), name };
    this.markChanged();
  }

  setFolderIcon(operation: OrganizationOperation, icon: string): void {
    if (!this.proposal) return;
    operation.after = { ...((operation.after as Record<string, unknown>) || {}), icon };
    this.markChanged();
  }

  setOperationTarget(operation: OrganizationOperation, targetId: string): void {
    operation.after = targetId;
    this.markChanged();
  }

  removeOperation(operationId: string): void {
    if (!this.proposal) return;
    this.proposal.operations = this.proposal.operations.filter(item => item.operation_id !== operationId);
    this.markChanged();
  }

  conversationName(id?: string): string {
    return this.sessions.find(session => session.id === id)?.name ?? id ?? '';
  }

  private markChanged(): void {
    if (!this.proposal) return;
    this.proposal.status = 'draft';
    this.proposalChanged.emit(this.proposal);
  }

  formatDate(timestamp: number): string {
    return new Date(timestamp * 1000).toLocaleString('de-DE');
  }
}
