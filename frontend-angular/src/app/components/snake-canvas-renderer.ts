const SNAKE_COLORS: Record<string, string> = {
  mint: '#7fffd4', cyan: '#22d3ee', violet: '#a78bfa', amber: '#fbbf24', rose: '#fb7185',
};

export function renderSnakeCanvas(canvas: HTMLCanvasElement, payload: Record<string, unknown>): void {
  const context = canvas.getContext('2d');
  if (!context) return;
  const boardWidth = Math.max(1, Number(payload['board_w']) || 24);
  const boardHeight = Math.max(1, Number(payload['board_h']) || 8);
  const width = canvas.width;
  const height = canvas.height;
  const cellWidth = width / boardWidth;
  const cellHeight = height / boardHeight;
  const color = SNAKE_COLORS[String(payload['snake_color'] || 'mint')] ?? SNAKE_COLORS['mint'];

  context.fillStyle = '#0b1220';
  context.fillRect(0, 0, width, height);
  context.strokeStyle = '#131e36';
  context.lineWidth = 0.5;
  for (let x = 0; x <= boardWidth; x++) {
    context.beginPath(); context.moveTo(x * cellWidth, 0); context.lineTo(x * cellWidth, height); context.stroke();
  }
  for (let y = 0; y <= boardHeight; y++) {
    context.beginPath(); context.moveTo(0, y * cellHeight); context.lineTo(width, y * cellHeight); context.stroke();
  }

  const trail = Array.isArray(payload['trail_path']) ? payload['trail_path'] as number[][] : [];
  context.fillStyle = color + '22';
  for (const [x, y] of trail) {
    context.fillRect(x * cellWidth + 1, y * cellHeight + 1, cellWidth - 2, cellHeight - 2);
  }
  const snake = Array.isArray(payload['snake']) ? payload['snake'] as number[][] : [];
  context.fillStyle = color + 'aa';
  for (let index = 1; index < snake.length; index++) {
    const [x, y] = snake[index];
    context.fillRect(x * cellWidth + 1, y * cellHeight + 1, cellWidth - 2, cellHeight - 2);
  }
  if (snake.length > 0) {
    context.fillStyle = color;
    const [headX, headY] = snake[0];
    context.fillRect(headX * cellWidth, headY * cellHeight, cellWidth, cellHeight);
  }
  if (payload['paused']) {
    context.fillStyle = 'rgba(11,18,32,0.65)';
    context.fillRect(0, 0, width, height);
    context.fillStyle = color;
    context.font = `bold ${Math.round(cellHeight * 0.75)}px ui-monospace,monospace`;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText('PAUSED', width / 2, height / 2);
  }
}
