import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';

import { ConfigGraphNode, nodeColor } from '../models/config-graph.model';

@Component({
  standalone: true,
  selector: 'app-config-graph-node-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  templateUrl: './config-graph-node-detail.component.html',
  styleUrls: ['./config-graph-node-detail.component.scss'],
})
export class ConfigGraphNodeDetailComponent {
  @Input() node: ConfigGraphNode | null = null;
  @Input() editMode = false;
  @Output() closed = new EventEmitter<void>();
  @Output() removeRequested = new EventEmitter<string>();

  readonly nodeColor = nodeColor;

  get dataEntries(): [string, unknown][] {
    if (!this.node) return [];
    return Object.entries(this.node.data).filter(([, v]) => v !== '' && v !== null && v !== undefined);
  }

  isArray(v: unknown): boolean {
    return Array.isArray(v);
  }

  asArray(v: unknown): unknown[] {
    return Array.isArray(v) ? v : [];
  }
}
