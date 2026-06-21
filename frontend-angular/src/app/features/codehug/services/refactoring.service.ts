import { Injectable, inject } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { PolicyService } from './policy.service';
import {
  ChRefactorProposalReadModel,
  ChRefactorProposalInput,
  ChRefactorDiffReadModel,
  ChRefactorApplyResult,
  ChServiceError,
} from '../models/codehug.models';

/**
 * RefactoringService — CH-005: Refactoring-Vorschlaege, Diff-Vorschau, Apply.
 *
 * SOLID: SRP — Vorschlaege erzeugen, Diffs zeigen, sicher anwenden.
 * Sicherheit: Apply nur im write-armed Modus.
 */
@Injectable({ providedIn: 'root' })
export class RefactoringService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);
  private readonly policy = inject(PolicyService);

  private hubUrl(): string {
    const h = this.dir.list().find(a => a.role === 'hub');
    if (!h) throw new ChServiceError('not_found', 'Kein Hub-Agent registriert');
    return h.url;
  }

  /**
   * Generiert Refactoring-Vorschlaege fuer ein Ziel.
   * Deterministische Plan-Phase, kein LLM-Roundtrip noetig.
   */
  propose(input: ChRefactorProposalInput): Observable<ChRefactorProposalReadModel[]> {
    const url = `${this.hubUrl()}/api/refactoring/propose`;
    return this.hub.post<ChRefactorProposalReadModel[]>(url, input, this.hubUrl()).pipe(
      map(arr => (arr ?? []).map(p => this.normalizeProposal(p))),
      catchError(err => throwError(() => this.toChError(err, 'propose'))),
    );
  }

  /**
   * Liefert die Diff-Vorschau fuer einen Vorschlag.
   */
  previewDiff(proposalId: string): Observable<ChRefactorDiffReadModel> {
    const url = `${this.hubUrl()}/api/refactoring/proposals/${encodeURIComponent(proposalId)}/diff`;
    return this.hub.get<ChRefactorDiffReadModel>(url, this.hubUrl()).pipe(
      map(d => this.normalizeDiff(d)),
      catchError(err => throwError(() => this.toChError(err, 'previewDiff'))),
    );
  }

  /**
   * Wendet einen Vorschlag an. Erfordert aktiven write-Modus.
   */
  apply(proposalId: string): Observable<ChRefactorApplyResult> {
    if (!this.policy.writeModeActive()) {
      return throwError(() => new ChServiceError(
        'forbidden',
        'Refactoring.Apply erfordert write-armed Modus. Aktiviere ihn zuerst.',
      ));
    }
    const url = `${this.hubUrl()}/api/refactoring/proposals/${encodeURIComponent(proposalId)}/apply`;
    return this.hub.post<ChRefactorApplyResult>(url, {}, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'apply'))),
    );
  }

  /**
   * Verwirft einen Vorschlag.
   */
  dismiss(proposalId: string): Observable<void> {
    const url = `${this.hubUrl()}/api/refactoring/proposals/${encodeURIComponent(proposalId)}`;
    return this.hub.delete<void>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'dismiss'))),
    );
  }

  private toChError(err: unknown, op: string): ChServiceError {
    let code: any = 'unknown';
    let message = `${op} failed`;
    if (err instanceof Error) message = `${op}: ${err.message}`;
    if (typeof err === 'object' && err !== null) {
      const status = (err as any).status;
      if (status === 403) code = 'forbidden';
      else if (status === 422) code = 'validation_error';
      else if (status === 0) code = 'network_error';
      else if (typeof status === 'number' && status >= 500) code = 'backend_error';
    }
    return new ChServiceError(code, message, err);
  }

  private normalizeProposal(p: any): ChRefactorProposalReadModel {
    return {
      id: p.id ?? '',
      kind: p.kind ?? 'optimize_imports',
      title: p.title ?? '',
      description: p.description ?? '',
      affectedFiles: p.affected_files ?? p.affectedFiles ?? [],
      affectedSymbols: p.affected_symbols ?? p.affectedSymbols ?? [],
      generatedBy: (p.generated_by ?? p.generatedBy ?? 'deterministic') as 'deterministic' | 'llm',
      confidence: typeof p.confidence === 'number' ? p.confidence : 0.9,
      layerSet: p.layer_set ?? p.layerSet,
      createdAt: p.created_at ?? p.createdAt ?? 0,
      status: p.status ?? 'open',
    };
  }

  private normalizeDiff(d: any): ChRefactorDiffReadModel {
    const validation = d.validation ?? {};
    return {
      proposalId: d.proposalId ?? d.proposal_id ?? '',
      hunks: (d.hunks ?? []).map((h: any) => ({
        filePath: h.filePath ?? h.file_path ?? '',
        oldStart: h.oldStart ?? h.old_start ?? 0,
        oldLines: h.oldLines ?? h.old_lines ?? 0,
        newStart: h.newStart ?? h.new_start ?? 0,
        newLines: h.newLines ?? h.new_lines ?? 0,
        unified: h.unified ?? '',
      })),
      validation: {
        syntaxOk: validation.syntaxOk ?? validation.syntax_ok ?? false,
        typeCheckOk: validation.typeCheckOk ?? validation.type_check_ok ?? false,
        linterOk: validation.linterOk ?? validation.linter_ok ?? false,
        diagnostics: validation.diagnostics ?? [],
      },
      generatedAt: d.generatedAt ?? d.generated_at ?? 0,
    };
  }
}