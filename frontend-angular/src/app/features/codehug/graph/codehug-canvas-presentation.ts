import { ChCanvasEdge, ChCanvasNode, ChNodeKind, ChNodeRunState } from './codehug-topology-layout';

export interface ChNodeStyle {
  fill: string;
  stroke: string;
  strokeWidth: number;
  textColor: string;
  icon: string;
}

export function nodeStyle(kind: ChNodeKind): ChNodeStyle {
  const styles: Record<ChNodeKind, ChNodeStyle> = {
    hub: { fill: '#fef3c7', stroke: '#d97706', strokeWidth: 2.5, textColor: '#78350f', icon: '⬡' },
    'worker-llm': { fill: '#dbeafe', stroke: '#2563eb', strokeWidth: 1.5, textColor: '#1e40af', icon: '◈' },
    'worker-det': { fill: '#f3f4f6', stroke: '#6b7280', strokeWidth: 1.5, textColor: '#374151', icon: '⚙' },
    'policy-layer': { fill: '#fff7ed', stroke: '#ea580c', strokeWidth: 1.5, textColor: '#7c2d12', icon: '⚖' },
    'test-layer': { fill: '#f0fdf4', stroke: '#16a34a', strokeWidth: 1.5, textColor: '#14532d', icon: '▣' },
    'routing-rule': { fill: '#f0fdfa', stroke: '#0d9488', strokeWidth: 1.5, textColor: '#134e4a', icon: '⤳' },
  };
  return styles[kind];
}

export function nodeFilter(node: ChCanvasNode): string | null {
  return node.runState === 'active'
    ? 'url(#ch-cv-glow-active)'
    : node.runState === 'completed'
      ? 'url(#ch-cv-glow-completed)'
      : node.runState === 'failed'
        ? 'url(#ch-cv-glow-failed)'
        : null;
}

export function edgePath(
  edge: ChCanvasEdge,
  nodes: ChCanvasNode[],
): { d: string; labelX: number; labelY: number } | null {
  const from = nodes.find(node => node.id === edge.fromId);
  const to = nodes.find(node => node.id === edge.toId);
  if (!from || !to) return null;
  const x1 = from.x + from.w / 2;
  const y1 = from.y + from.h;
  const x2 = to.x + to.w / 2;
  const y2 = to.y;
  const cpY = (y1 + y2) / 2;
  return {
    d: `M ${x1} ${y1} C ${x1} ${cpY}, ${x2} ${cpY}, ${x2} ${y2}`,
    labelX: (x1 + x2) / 2,
    labelY: (y1 + y2) / 2 - 4,
  };
}

export function badgeFill(node: ChCanvasNode): string {
  if (['online', 'healthy', 'on'].includes(node.badge ?? '')) return '#16a34a';
  if (['offline', 'unhealthy', 'off'].includes(node.badge ?? '')) return '#dc2626';
  return node.badge === 'degraded' ? '#f59e0b' : '#6b7280';
}

export function kindLabel(kind: ChNodeKind): string {
  return {
    hub: 'Hub-Instanz',
    'worker-llm': 'LLM-Worker',
    'worker-det': 'Deterministic Worker',
    'policy-layer': 'Policy-Layer',
    'test-layer': 'Test-/Instruktions-Layer',
    'routing-rule': 'Routing-Regel',
  }[kind];
}

export function runStateLabel(state: ChNodeRunState): string {
  return {
    idle: 'Inaktiv',
    active: 'Aktiv',
    completed: 'Abgeschlossen',
    failed: 'Fehler',
    skipped: 'Übersprungen',
  }[state];
}
