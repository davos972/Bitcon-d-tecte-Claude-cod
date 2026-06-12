export type TimeFrame = '5M' | '15M' | '1H';
export type Phase = 'ANALYSIS' | 'LOCK' | 'RESOLUTION';

export const CYCLE_SECONDS: Record<TimeFrame, number> = {
  '5M': 300,
  '15M': 900,
  '1H': 3600,
};

// Lock phase starts this many seconds before window close
export const LOCK_SECONDS: Record<TimeFrame, number> = {
  '5M': 45,
  '15M': 90,
  '1H': 360,
};

// Resolution phase lasts the first 5 seconds of the new window
export const RESOLUTION_SECONDS = 5;

/**
 * Returns the UTC-aligned window start for the given timestamp.
 * Windows are synchronized to the real UTC clock, never to app launch time.
 */
export function getWindowStart(nowSeconds: number, tf: TimeFrame): number {
  const cycle = CYCLE_SECONDS[tf];
  return Math.floor(nowSeconds / cycle) * cycle;
}

export function getPhaseInfo(nowSeconds: number, tf: TimeFrame): {
  phase: Phase;
  windowStart: number;
  elapsed: number;
  remaining: number;
  progress: number;
} {
  const cycle = CYCLE_SECONDS[tf];
  const lock = LOCK_SECONDS[tf];
  const windowStart = getWindowStart(nowSeconds, tf);
  const elapsed = nowSeconds - windowStart;
  const remaining = cycle - elapsed;
  const progress = Math.min(1, elapsed / cycle);

  let phase: Phase;
  if (elapsed < RESOLUTION_SECONDS) {
    // First 5 seconds of a new window = show result of previous window
    phase = 'RESOLUTION';
  } else if (remaining <= lock) {
    phase = 'LOCK';
  } else {
    phase = 'ANALYSIS';
  }

  return { phase, windowStart, elapsed, remaining: Math.max(0, remaining), progress };
}

/**
 * Polymarket slug for a given timeframe and window start.
 * 1H has no Polymarket market → returns null (scored locally).
 */
export function getPolySlug(tf: TimeFrame, windowStart: number): string | null {
  if (tf === '5M') return `btc-updown-5m-${windowStart}`;
  if (tf === '15M') return `btc-updown-15m-${windowStart}`;
  return null;
}

export function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`;
}
