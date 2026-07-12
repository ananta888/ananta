import { Injectable, signal } from '@angular/core';
@Injectable({providedIn:'root'})
export class VpNavigationService{
  readonly selectedRunId=signal<string>('');readonly selectedStepId=signal<string>('');readonly target=signal<'process'|'trace'>('process');
  showTrace(runId:string,stepId:string):void{this.selectedRunId.set(runId);this.selectedStepId.set(stepId);this.target.set('trace');}
  showProcess(runId:string,stepId:string):void{this.selectedRunId.set(runId);this.selectedStepId.set(stepId);this.target.set('process');}
}
