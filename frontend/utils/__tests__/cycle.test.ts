import {
  getWindowStart,
  getPhaseInfo,
  getPolySlug,
  formatCountdown,
  CYCLE_SECONDS,
  LOCK_SECONDS,
} from '../cycle';

describe('getWindowStart', () => {
  it('snaps to the UTC 15m grid', () => {
    // 13:07:30 UTC -> 13:00:00
    const t = Math.floor(Date.UTC(2024, 0, 1, 13, 7, 30) / 1000);
    const ws = getWindowStart(t, '15M');
    expect(ws % CYCLE_SECONDS['15M']).toBe(0);
    expect(t - ws).toBeGreaterThanOrEqual(0);
    expect(t - ws).toBeLessThan(CYCLE_SECONDS['15M']);
  });

  it('is exact on a boundary for every timeframe', () => {
    const base = Math.floor(Date.UTC(2024, 0, 1, 0, 0, 0) / 1000);
    for (const tf of ['5M', '15M', '1H'] as const) {
      expect(getWindowStart(base, tf)).toBe(base);
    }
  });
});

describe('getPhaseInfo', () => {
  const ws = Math.floor(Date.UTC(2024, 0, 1, 12, 0, 0) / 1000);

  it('is RESOLUTION in the first 5 seconds of a window', () => {
    expect(getPhaseInfo(ws + 2, '15M').phase).toBe('RESOLUTION');
  });

  it('is ANALYSIS in the middle of a window', () => {
    expect(getPhaseInfo(ws + 300, '15M').phase).toBe('ANALYSIS');
  });

  it('is LOCK within the final lock window', () => {
    const cycle = CYCLE_SECONDS['15M'];
    const lock = LOCK_SECONDS['15M'];
    expect(getPhaseInfo(ws + cycle - lock + 1, '15M').phase).toBe('LOCK');
  });

  it('reports remaining and progress consistently', () => {
    const info = getPhaseInfo(ws + 450, '15M');
    expect(info.windowStart).toBe(ws);
    expect(info.remaining).toBe(450);
    expect(info.progress).toBeCloseTo(0.5, 5);
  });
});

describe('getPolySlug', () => {
  it('builds deterministic 5m/15m slugs', () => {
    expect(getPolySlug('5M', 1700000100)).toBe('btc-updown-5m-1700000100');
    expect(getPolySlug('15M', 1700000100)).toBe('btc-updown-15m-1700000100');
  });

  it('returns null for 1H (no Polymarket market exists)', () => {
    expect(getPolySlug('1H', 1700000100)).toBeNull();
  });
});

describe('formatCountdown', () => {
  it('formats minutes:seconds and bare seconds', () => {
    expect(formatCountdown(125)).toBe('2:05');
    expect(formatCountdown(45)).toBe('45s');
  });
});
