import { useCallback, useEffect, useRef, useState } from 'react';
import { TimeFrame, CYCLE_SECONDS, getWindowStart } from '../utils/cycle';

const BACKEND = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';
const RETRY_DELAY_MS = 2500;
const MAX_RETRIES = 3;
const REFRESH_INTERVAL_MS = 12_000;

export interface MarkovData {
  prob_up: number;
  prob_down: number;
  direction: 'UP' | 'DOWN' | 'NONE';
  pattern: string[];
  recent_candles: string[];
  up_count: number;
  down_count: number;
  sample_size: number;
  n_used: number;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE';
  candle_count: number;
  last_closed_time: number;
  current_window_open: number | null;
  candle_source: string;
}

export type MarkovStatus = 'idle' | 'loading' | 'updating' | 'ok' | 'error';

export interface MarkovState {
  data: MarkovData | null;
  status: MarkovStatus;
  windowStart: number;
  isFresh: boolean;
  noEdge: boolean;
}

/**
 * Fetches Markov prediction for a given timeframe.
 *
 * Key behaviours from the guide:
 * - Passes current window_start to backend so it excludes the forming candle.
 * - Checks last_closed_time freshness: the pattern must use the most recent
 *   closed candle. If stale, retries up to 3 times with 2.5s delay.
 * - Shows "UPDATING" state during retries — never shows a stale prediction.
 * - Refreshes every 12 seconds and at each new window.
 */
export function useMarkov(tf: TimeFrame): MarkovState {
  const nowSec = () => Math.floor(Date.now() / 1000);

  const [state, setState] = useState<MarkovState>({
    data: null,
    status: 'idle',
    windowStart: getWindowStart(nowSec(), tf),
    isFresh: false,
    noEdge: false,
  });

  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const latestWindowRef = useRef<number>(getWindowStart(nowSec(), tf));

  const isNoEdge = (d: MarkovData) =>
    d.prob_up >= 0.45 && d.prob_up <= 0.55;

  const isFreshCheck = (d: MarkovData, windowStart: number): boolean => {
    // The PDF specifies the exact check:
    // last_closed_time must equal windowStart - cycleDuration
    // This ensures the pattern uses the candle immediately before the current window.
    const expectedLastClosed = windowStart - CYCLE_SECONDS[tf];
    return d.last_closed_time === expectedLastClosed;
  };

  const fetchData = useCallback(
    async (windowStart: number, isRetry = false) => {
      if (!isRetry) {
        retryCountRef.current = 0;
        setState((prev) => ({
          ...prev,
          status: prev.data ? 'updating' : 'loading',
          windowStart,
        }));
      }

      try {
        const url = `${BACKEND}/api/markov?mode=${tf}&window=${windowStart}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: MarkovData = await res.json();

        // Freshness check: ensure we got a freshly updated pattern
        const fresh = isFreshCheck(data, windowStart);

        if (!fresh && retryCountRef.current < MAX_RETRIES) {
          // Pattern is stale (API hasn't published the new closed candle yet)
          // Show UPDATING and retry after 2.5s
          retryCountRef.current += 1;
          setState((prev) => ({ ...prev, status: 'updating' }));
          retryTimerRef.current = setTimeout(
            () => fetchData(windowStart, true),
            RETRY_DELAY_MS
          );
          return;
        }

        setState({
          data,
          status: 'ok',
          windowStart,
          isFresh: fresh,
          noEdge: isNoEdge(data),
        });
      } catch {
        setState((prev) => ({ ...prev, status: 'error' }));
      }
    },
    [tf]
  );

  useEffect(() => {
    const tick = () => {
      const now = nowSec();
      const ws = getWindowStart(now, tf);
      const windowChanged = ws !== latestWindowRef.current;

      if (windowChanged) {
        latestWindowRef.current = ws;
        if (retryTimerRef.current) {
          clearTimeout(retryTimerRef.current);
          retryTimerRef.current = null;
        }
        fetchData(ws);
      } else {
        // Periodic refresh within the same window
        fetchData(ws);
      }
    };

    // Initial fetch
    const ws = getWindowStart(nowSec(), tf);
    latestWindowRef.current = ws;
    fetchData(ws);

    // Refresh every 12 seconds
    intervalRef.current = setInterval(tick, REFRESH_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    };
  }, [tf, fetchData]);

  return state;
}
