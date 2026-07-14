export type VoiceDiffOperation = 'equal' | 'insert' | 'delete';

export interface VoiceDiffChunk {
  operation: VoiceDiffOperation;
  text: string;
}

const MAX_DIFF_TOKENS = 500;

/** A bounded word-level diff used only for presentation; Hub edits remain authoritative. */
export function voiceTranscriptDiff(original: string, corrected: string): VoiceDiffChunk[] {
  const left = tokens(original);
  const right = tokens(corrected);
  if (left.join(' ') === right.join(' ')) return left.length ? [{ operation: 'equal', text: left.join(' ') }] : [];
  if (left.length > MAX_DIFF_TOKENS || right.length > MAX_DIFF_TOKENS) {
    return [
      ...(left.length ? [{ operation: 'delete' as const, text: left.join(' ') }] : []),
      ...(right.length ? [{ operation: 'insert' as const, text: right.join(' ') }] : []),
    ];
  }

  const lengths: number[][] = Array.from({ length: left.length + 1 }, () => (
    Array<number>(right.length + 1).fill(0)
  ));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      lengths[i][j] = left[i] === right[j]
        ? lengths[i + 1][j + 1] + 1
        : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }

  const result: VoiceDiffChunk[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      append(result, 'equal', left[i]);
      i += 1;
      j += 1;
    } else if (j < right.length && (i >= left.length || lengths[i][j + 1] >= lengths[i + 1][j])) {
      append(result, 'insert', right[j]);
      j += 1;
    } else {
      append(result, 'delete', left[i]);
      i += 1;
    }
  }
  return result;
}

function tokens(value: string): string[] {
  return String(value || '').trim().split(/\s+/).filter(Boolean);
}

function append(chunks: VoiceDiffChunk[], operation: VoiceDiffOperation, token: string): void {
  const previous = chunks[chunks.length - 1];
  if (previous?.operation === operation) previous.text += ` ${token}`;
  else chunks.push({ operation, text: token });
}
