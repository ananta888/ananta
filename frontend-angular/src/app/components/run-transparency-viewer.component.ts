import { Component, Input, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  RunTransparencyReport, RunStepTrace, PolicyBlockade,
  ContextTraceSummary, DelegationTraceSummary, ToolCallSummary,
  DiffProposalSummary, EvidenceSummary, ApprovalSummary
} from '../models/transparency.models';

@Component({
  standalone: true,
  selector: 'app-run-transparency-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  styles: [`
    .transparency-viewer { font-size: 0.92rem; }
    .step-card { border: 1px solid var(--border, #e0e0e0); border-radius: 6px; padding: 1rem; margin-bottom: 0.75rem; }
    .step-header { display: flex; align-items: center; gap: 0.5rem; font-weight: 600; }
    .badge { padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.78rem; font-weight: 600; }
    .badge-external { background: #fff3cd; color: #856404; }
    .badge-local { background: #d1ecf1; color: #0c5460; }
    .badge-blocked { background: #f8d7da; color: #721c24; }
    .badge-approved { background: #d4edda; color: #155724; }
    .badge-pending { background: #cce5ff; color: #004085; }
    .evidence-section { margin-top: 0.5rem; }
    .model-claim { color: #666; font-style: italic; }
    .verified-fact { color: #155724; font-weight: 500; }
    .blockade-hard { background: #f8d7da; padding: 0.4rem 0.6rem; border-radius: 4px; margin: 0.25rem 0; }
    .blockade-warn { background: #fff3cd; padding: 0.4rem 0.6rem; border-radius: 4px; margin: 0.25rem 0; }
    .context-trace { background: #f8f9fa; border-left: 3px solid #6c757d; padding: 0.5rem 0.75rem; margin: 0.5rem 0; font-size: 0.85rem; }
    .tool-log { font-family: monospace; font-size: 0.82rem; }
    .section-label { font-weight: 600; color: #495057; margin-top: 0.75rem; margin-bottom: 0.25rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .diff-proposal { border: 1px solid #dee2e6; border-radius: 4px; padding: 0.5rem; margin: 0.3rem 0; }
    .risk-critical { color: #721c24; font-weight: 600; }
    .risk-high { color: #856404; font-weight: 600; }
    .risk-low { color: #155724; }
    .confidence-bar { height: 4px; background: #e9ecef; border-radius: 2px; margin-top: 2px; }
    .confidence-fill { height: 100%; border-radius: 2px; background: #28a745; }
  `],
  template: `
    <div class="transparency-viewer">
      @if (!report) {
        <p class="muted">Kein Transparency-Report verfügbar.</p>
      }

      @if (report) {
        <!-- Header -->
        <div class="row gap-sm align-center mb-md">
          <span class="section-label">Run {{ report.run_id | slice:0:8 }}</span>
          @if (report.local_only_mode) {
            <span class="badge badge-local">Local Only</span>
          }
          @if (report.has_external_providers) {
            <span class="badge badge-external">Externe Provider</span>
          }
          @if (report.total_policy_blockades > 0) {
            <span class="badge badge-blocked">{{ report.total_policy_blockades }} Blockaden</span>
          }
          @if (report.verification_hash) {
            <span class="badge badge-approved" title="{{ report.verification_hash }}">Verifiziert</span>
          }
        </div>

        <!-- Steps -->
        @for (step of report.steps; track step.step_id) {
          <div class="step-card">
            <div class="step-header">
              <span>{{ step.step_name }}</span>
              <span class="badge" [class]="stateClass(step.state)">{{ step.state }}</span>
              @if (step.local_only) {
                <span class="badge badge-local" title="Kein externer Provider">Local</span>
              }
              @if (hasExternalEvidence(step)) {
                <span class="badge badge-external">Extern</span>
              }
              @if (step.duration_ms !== null) {
                <span class="muted font-sm">{{ step.duration_ms }}ms</span>
              }
            </div>

            <!-- Policy Blockaden -->
            @if (step.policy_blockades?.length) {
              <div class="section-label">Policy-Blockaden</div>
              @for (b of step.policy_blockades; track b.rule) {
                <div [class]="blockadeClass(b)">
                  <strong>{{ b.action_attempted }}</strong>: {{ b.blocked_reason }}
                  <span class="muted font-sm"> [{{ b.rule }}]</span>
                </div>
              }
            }

            <!-- Context Trace -->
            @if (step.context_trace) {
              <div class="section-label">Context Trace</div>
              <div class="context-trace">
                <strong>{{ step.context_trace.provider }}</strong>:
                {{ step.context_trace.selected_count }} Treffer,
                {{ step.context_trace.discarded_count }} verworfen
                ({{ step.context_trace.budget_chars_used }}/{{ step.context_trace.budget_chars_limit }} Zeichen)
                @if (step.context_trace.has_external_evidence) {
                  <span class="badge badge-external ml-xs">Externer Provider</span>
                }
                @if (step.context_trace.policy_decisions?.length) {
                  <div class="muted font-sm mt-xs">Policy: {{ step.context_trace.policy_decisions.join(', ') }}</div>
                }
              </div>
            }

            <!-- Delegation Trace -->
            @if (step.delegation_trace) {
              <div class="section-label">Delegation</div>
              <div class="context-trace">
                Worker: <strong>{{ step.delegation_trace.chosen_worker_id }}</strong>
                @if (step.delegation_trace.chosen_expert_id) {
                  (Expert: {{ step.delegation_trace.chosen_expert_id }})
                }
                <br>
                Grund: {{ step.delegation_trace.selection_reason }}
                <br>
                Tools: <code>{{ step.delegation_trace.tools_granted.join(', ') || 'keine' }}</code>
                @if (step.delegation_trace.alternatives_considered?.length) {
                  <div class="muted font-sm mt-xs">
                    Alternativen verworfen:
                    @for (alt of step.delegation_trace.alternatives_considered; track alt.worker_id) {
                      {{ alt.worker_id }} ({{ alt.reason_not_chosen }})
                    }
                  </div>
                }
              </div>
            }

            <!-- Tool Calls -->
            @if (step.tool_calls?.length) {
              <div class="section-label">Tool Calls ({{ step.tool_calls.length }})</div>
              <div class="tool-log">
                @for (tc of step.tool_calls; track tc.tool_call_id) {
                  <div class="row gap-sm align-center mb-xs">
                    <span class="badge" [class]="policyClass(tc.policy_decision)">{{ tc.policy_decision }}</span>
                    <span>{{ tc.tool_name }}</span>
                    <span class="muted">{{ tc.duration_ms }}ms</span>
                    @if (tc.redaction_applied) {
                      <span class="badge badge-local">redacted</span>
                    }
                  </div>
                }
              </div>
            }

            <!-- Diff Proposals -->
            @if (step.diff_proposals?.length) {
              <div class="section-label">Diff Proposals</div>
              @for (dp of step.diff_proposals; track dp.proposal_id) {
                <div class="diff-proposal">
                  <div class="row space-between align-center">
                    <span>
                      +{{ dp.total_lines_added }} / -{{ dp.total_lines_removed }}
                      ({{ dp.total_files }} Dateien)
                    </span>
                    <div class="row gap-xs">
                      <span [class]="riskClass(dp.risk_summary)">{{ dp.risk_summary }}</span>
                      <span class="badge" [class]="proposalStatusClass(dp.status)">{{ dp.status }}</span>
                    </div>
                  </div>
                </div>
              }
            }

            <!-- Approval Gates -->
            @if (step.approval_gates?.length) {
              <div class="section-label">Approval Gates</div>
              @for (ag of step.approval_gates; track ag.gate_id) {
                <div class="row gap-sm align-center mb-xs">
                  <span class="badge" [class]="approvalClass(ag.status)">{{ ag.status }}</span>
                  <span>{{ ag.gate_type }}</span>
                  <span class="muted font-sm">{{ ag.risk_level }}</span>
                  @if (ag.decided_by) {
                    <span class="muted font-sm">von {{ ag.decided_by }}</span>
                  }
                </div>
              }
            }

            <!-- Evidence (getrennt von Modellaussagen) -->
            @if (step.evidence?.length || step.model_claims?.length || step.verified_facts?.length) {
              <div class="section-label">Evidence vs. Modellaussagen</div>
              <div class="evidence-section">
                @for (e of step.evidence; track e.source_file) {
                  <div class="mb-xs">
                    <span class="muted font-sm">{{ e.evidence_type }}</span>
                    <span class="ml-xs">{{ e.source_file }}</span>
                    <div class="confidence-bar">
                      <div class="confidence-fill" [style.width.%]="e.confidence * 100"></div>
                    </div>
                  </div>
                }
                @for (claim of step.model_claims; track claim) {
                  <div class="model-claim">Modell: {{ claim }}</div>
                }
                @for (fact of step.verified_facts; track fact) {
                  <div class="verified-fact">✓ {{ fact }}</div>
                }
              </div>
            }
          </div>
        }
      }
    </div>
  `,
})
export class RunTransparencyViewerComponent {
  @Input() report: RunTransparencyReport | null = null;

  stateClass(state: string): string {
    const map: Record<string, string> = {
      completed: 'badge-approved',
      failed: 'badge-blocked',
      running: 'badge-pending',
      waiting_for_approval: 'badge-pending',
      cancelled: 'badge-blocked',
    };
    return map[state] ?? 'badge';
  }

  blockadeClass(b: PolicyBlockade): string {
    return b.severity === 'hard_block' ? 'blockade-hard' : 'blockade-warn';
  }

  policyClass(decision: string): string {
    return decision === 'allowed' ? 'badge-approved' : 'badge-blocked';
  }

  proposalStatusClass(status: string): string {
    const map: Record<string, string> = {
      approved: 'badge-approved',
      rejected: 'badge-blocked',
      pending_approval: 'badge-pending',
      applied: 'badge-approved',
    };
    return map[status] ?? 'badge';
  }

  approvalClass(status: string): string {
    const map: Record<string, string> = {
      approved: 'badge-approved',
      denied: 'badge-blocked',
      pending: 'badge-pending',
      expired: 'badge-blocked',
    };
    return map[status] ?? 'badge';
  }

  riskClass(risk: string): string {
    const map: Record<string, string> = {
      critical: 'risk-critical',
      high: 'risk-high',
      low: 'risk-low',
    };
    return map[risk] ?? '';
  }

  hasExternalEvidence(step: RunStepTrace): boolean {
    return step.context_trace?.has_external_evidence ?? false;
  }
}
