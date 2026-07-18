import { EdgeVisualStyle, NodeVisualStyle } from '../../models/graph-visual-metrics.model';

export type GraphTooltipStyle = Readonly<NodeVisualStyle | EdgeVisualStyle>;

/** Canonical, renderer-independent tooltip text. No HTML is accepted or returned. */
export function graphVisualTooltipText(label: string, style: GraphTooltipStyle): string {
  return [
    label,
    `Gesamtscore: ${style.score.toFixed(6)} · ${style.availability} · ${style.scoreState}`,
    ...style.breakdown.map(item => {
      const provenance = item.provenance
        ? ` source=${item.provenance.source} algorithm=${item.provenance.algorithmVersion}`
        : '';
      return `${item.metricId}: raw=${item.rawValue ?? '–'} normalized=${item.normalizedValue ?? '–'} ` +
        `weight=${item.weight} direction=${item.direction} partial=${item.partialScore} ` +
        `availability=${item.availability}${provenance}` +
        `${item.reasonCode ? ` reason=${item.reasonCode}` : ''}`;
    }),
  ].join('\n');
}

/** HTMLElement labels are populated only through textContent to prevent XSS. */
export function graphVisualTooltipElement(text: string): HTMLElement {
  const tooltip = document.createElement('div');
  tooltip.textContent = text;
  tooltip.style.whiteSpace = 'pre-wrap';
  tooltip.style.fontFamily = 'ui-monospace, monospace';
  return tooltip;
}
