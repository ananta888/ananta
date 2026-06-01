import { Component, Input } from '@angular/core';
import { NgFor, NgIf } from '@angular/common';
import { CcPolicySnapshot } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';

@Component({
  standalone: true,
  selector: 'app-control-center-security-inspector',
  imports: [NgFor, NgIf, StatusChipComponent],
  template: `
    <section class="inspector" *ngIf="policy">
      <h4>Security Inspector</h4>
      <div class="row"><span>Policy</span><strong>{{ policy.policyVersion }}</strong></div>
      <div class="row"><span>Risk</span><app-status-chip [label]="policy.riskLevel" [tone]="riskTone(policy.riskLevel)" /></div>
      <div class="row"><span>Approval</span><app-status-chip [label]="policy.requiresHumanApproval ? 'required' : 'none'" [tone]="policy.requiresHumanApproval ? 'warn':'ok'" /></div>

      <h5>Allowed Tools</h5>
      <ul><li *ngFor="let t of policy.allowedTools">{{ t }}</li></ul>

      <h5>Denied Tools</h5>
      <ul><li *ngFor="let t of policy.deniedTools">{{ t }}</li></ul>

      <h5>Allowed Paths</h5>
      <ul><li *ngFor="let p of policy.allowedPaths">{{ p }} <span *ngIf="isSensitivePath(p)" class="warn">[sensitive]</span></li></ul>

      <h5>Denied Paths</h5>
      <ul><li *ngFor="let p of policy.deniedPaths">{{ p }} <span *ngIf="isSensitivePath(p)" class="warn">[secret path]</span></li></ul>
      <p class="warn" *ngIf="cloudBoundaryWarning()">Cloud-Warnung: Sensitive Pfade erkannt, Boundary sollte local-only sein.</p>

      <p class="muted" *ngIf="policy.approvalReason">Grund: {{ policy.approvalReason }}</p>
    </section>
  `,
  styles: [`.inspector{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0b1220}.row{display:flex;justify-content:space-between;align-items:center;margin:6px 0}.muted{color:#94a3b8;font-size:12px}.warn{color:#fdba74}ul{margin:6px 0 10px 16px}`]
})
export class ControlCenterSecurityInspectorComponent {
  @Input() policy!: CcPolicySnapshot;
  riskTone(r: CcPolicySnapshot['riskLevel']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    return r === 'low' ? 'ok' : r === 'medium' ? 'info' : r === 'high' ? 'warn' : 'danger';
  }

  isSensitivePath(path: string): boolean {
    const p = String(path || '').toLowerCase();
    return p.includes('.env') || p.includes('/secrets') || p.includes('secret') || p.endsWith('.pem') || p.endsWith('.key');
  }

  cloudBoundaryWarning(): boolean {
    const allPaths = [...(this.policy?.allowedPaths || []), ...(this.policy?.deniedPaths || [])];
    return allPaths.some((p) => this.isSensitivePath(p));
  }
}
