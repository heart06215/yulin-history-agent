export interface DiffPart {
  type: 'same' | 'added' | 'removed';
  text: string;
}

function segment(text: string): string[] {
  return text.split(/(?<=[。！？；，、\n])/u).filter((s) => s.length > 0);
}

/** Segment-level diff between two texts; no HTML is generated. */
export function diffSegments(before: string, after: string): DiffPart[] {
  const x = segment(before);
  const y = segment(after);
  const n = x.length;
  const m = y.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    Array.from({ length: m + 1 }, () => 0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        x[i] === y[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffPart[] = [];
  const push = (type: DiffPart['type'], text: string) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.type === type) last.text += text;
    else out.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) {
      push('same', x[i]);
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push('removed', x[i]);
      i += 1;
    } else {
      push('added', y[j]);
      j += 1;
    }
  }
  while (i < n) push('removed', x[i++]);
  while (j < m) push('added', y[j++]);
  return out;
}
