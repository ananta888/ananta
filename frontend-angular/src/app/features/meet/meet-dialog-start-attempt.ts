import { signal } from '@angular/core';
import type { Observable } from 'rxjs';
import type { DialogStartReceipt } from './meet-dialog-start-request';

/** Component-local unresolved operation; no scheduler, storage or automatic retry. */
export class PendingDialogStart {
  readonly pending = signal(false);
  private operation?: Observable<DialogStartReceipt>;

  begin(operation: Observable<DialogStartReceipt>): void {
    if (this.operation) throw new Error('meet_dialog_start_already_pending');
    this.operation = operation;
    this.pending.set(true);
  }

  current(): Observable<DialogStartReceipt> | undefined { return this.operation; }

  clear(): void {
    this.operation = undefined;
    this.pending.set(false);
  }
}
