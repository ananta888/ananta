import { Component, Input, Output, EventEmitter, OnChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MermaidDiagramComponent } from './mermaid-diagram.component';

interface TextSeg { kind: 'text'; content: string; }
interface MermaidSeg { kind: 'mermaid'; code: string; }
type Segment = TextSeg | MermaidSeg;

function parseSegments(text: string): Segment[] {
  const segs: Segment[] = [];
  const re = /```\s*mermaid[^\n]*\r?\n([\s\S]*?)```/gi;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segs.push({ kind: 'text', content: text.slice(last, m.index) });
    segs.push({ kind: 'mermaid', code: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) segs.push({ kind: 'text', content: text.slice(last) });
  return segs;
}

@Component({
  standalone: true,
  selector: 'app-chat-message',
  imports: [CommonModule, MermaidDiagramComponent],
  template: `
    @for (seg of segments; track $index) {
      @if (seg.kind === 'mermaid') {
        <app-mermaid-diagram [code]="seg.code" (retryRequest)="onRetry($event)" />
      } @else {
        <span class="text-seg">{{ seg.content }}</span>
      }
    }
  `,
  styles: [`
    :host { display: block; }
    .text-seg { white-space: pre-wrap; word-break: break-word; }
  `],
})
export class ChatMessageComponent implements OnChanges {
  @Input() text = '';
  @Output() retryRequest = new EventEmitter<string>();
  segments: Segment[] = [];

  ngOnChanges(): void {
    this.segments = parseSegments(this.text);
  }

  onRetry(code: string): void {
    this.retryRequest.emit(code);
  }
}
