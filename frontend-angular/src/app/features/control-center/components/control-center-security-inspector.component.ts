import { Component, Input } from '@angular/core';

import { CcPolicySnapshot } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';

@Component({
  standalone: true,
  selector: 'app-control-center-security-inspector',
  imports: [StatusChipComponent],
  template: `
    @if (policy) {
      <section class="inspector">
        <h4>Security Inspector</h4>
        <div class="row"><span>Policy</span><strong>{{ policy.policyVersion }}</strong></div>
        <div class="row"><span>Risk</span><app-status-chip [label]="policy.riskLevel" [tone]="riskTone(policy.riskLevel)" /></div>
        <div class="row"><span>Approval</span><app-status-chip [label]="policy.requiresHumanApproval ? 'required' : 'none'" [tone]="policy.requiresHumanApproval ? 'warn':'ok'" /></div>
        <div class="row"><span>Cloud Allowed</span><strong>{{ policy.cloudAllowed === null ? 'unknown' : (policy.cloudAllowed ? 'yes' : 'no') }}</strong></div>
        <div class="row"><span>Runtime Boundary</span><strong>{{ policy.runtimeBoundary }}</strong></div>
        <h5>Allowed Tools</h5>
        <ul>@for (t of policy.allowedTools; track t) {
          <li>{{ t }}</li>
        }</ul>
        <h5>Denied Tools</h5>
        <ul>@for (t of policy.deniedTools; track t) {
          <li>{{ t }}</li>
        }</ul>
        <h5>Allowed Paths</h5>
        <ul>@for (p of policy.allowedPaths; track p) {
          <li>{{ p }} @if (isSensitivePath(p)) {
            <span class="warn">[sensitive]</span>
          }</li>
        }</ul>
        <h5>Denied Paths</h5>
        <ul>@for (p of policy.deniedPaths; track p) {
          <li>{{ p }} @if (isSensitivePath(p)) {
            <span class="warn">[secret path]</span>
          }</li>
        }</ul>
        @if (cloudBoundaryWarning()) {
          <p class="warn">Cloud-Warnung: Sensitive erlaubte Pfade erkannt und Cloud ist erlaubt.</p>
        }
        @if (policy.approvalReason) {
          <p class="muted">Grund: {{ policy.approvalReason }}</p>
        }
      </section>
    }
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
    const cloudAllowed = this.policy?.cloudAllowed === true || this.policy?.runtimeBoundary === 'cloud-allowed';
    if (!cloudAllowed) return false;
    const allowedPaths = this.policy?.allowedPaths || [];
    return allowedPaths.some((p) => this.isSensitivePath(p));
  }
}
