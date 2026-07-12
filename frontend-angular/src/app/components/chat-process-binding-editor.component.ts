import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatProcessRef, ChatSessionsService } from '../services/chat-sessions.service';
import { VisualProcessApiService, SavedGraphSummary } from '../features/visual-process/visual-process-api.service';
import { VisualProcessEditorComponent } from '../features/visual-process/visual-process-editor.component';
import { emptyGraph } from '../features/visual-process/vp-editor-config';

@Component({selector:'app-chat-process-binding-editor',standalone:true,imports:[CommonModule,FormsModule,VisualProcessEditorComponent],template:`
  <section class="binding"><div class="actions">
    @if (sessionId) { <button (click)="inherit()" [class.active]="!processRef" [disabled]="readOnly">Profilprozess erben</button> }
    <select [ngModel]="processRef?.graph_id||''" (ngModelChange)="choose($event)" [disabled]="readOnly"><option value="">Kein Prozess</option>
      @for(graph of graphs;track graph.id){<option [value]="graph.id">{{graph.name}} · v{{graph.version||'1.0'}} · {{graph.origin||'custom'}}</option>}
    </select>
    <button (click)="create()" [disabled]="readOnly">Neu erstellen</button>
    @if(sessionId&&processRef){<button (click)="clone()" [disabled]="readOnly">Als Session-Kopie bearbeiten</button>}
  </div>
  @if(status){<p role="status">{{status}}</p>}
  @if(processRef){<app-visual-process-editor [graphId]="processRef.graph_id" editorMode="embedded-edit" />}
  </section>`,styles:[`.actions{display:flex;gap:7px;flex-wrap:wrap}.active{outline:1px solid #4ea1ff}.binding{display:grid;gap:8px}`]})
export class ChatProcessBindingEditorComponent implements OnInit{
  private api=inject(VisualProcessApiService);private sessions=inject(ChatSessionsService);
  @Input() sessionId='';@Input() processRef:ChatProcessRef|null=null;@Input() profileProcessRef:ChatProcessRef|null=null;
  @Input() readOnly=false;
  @Output() processRefChange=new EventEmitter<ChatProcessRef|null>();
  graphs:SavedGraphSummary[]=[];status='';
  ngOnInit():void{this.reload();}
  reload():void{this.api.listSavedGraphs().subscribe({next:g=>this.graphs=g,error:()=>this.status='Prozessliste nicht verfügbar'});}
  choose(graphId:string):void{const ref=graphId?{graph_id:graphId,version:'latest'}:null;this.persist(ref);}
  inherit():void{this.persist(null);}
  clone():void{if(!this.sessionId)return;this.sessions.cloneEffectiveProcess(this.sessionId).subscribe({next:r=>{this.processRef=r.process_ref;this.status='Session-Kopie erstellt';this.reload();},error:e=>this.status=e?.error?.error||'Klonen fehlgeschlagen'});}
  create():void{const graph=emptyGraph();graph.name='Neuer Chat-Prozess';this.api.saveGraph(graph).subscribe({next:()=>{this.reload();this.persist({graph_id:graph.id,version:graph.version});},error:()=>this.status='Prozess konnte nicht erstellt werden'});}
  private persist(ref:ChatProcessRef|null):void{this.processRef=ref;this.processRefChange.emit(ref);if(!this.sessionId)return;this.sessions.updateProcessRef(this.sessionId,ref).subscribe({next:()=>this.status=ref?'Prozess zugeordnet':'Profilvererbung aktiv',error:e=>this.status=e?.error?.error||'Zuordnung fehlgeschlagen'});}
}
