import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useEffect, useRef, useState } from 'react';
import { TimeFrame, CYCLE_SECONDS, getPolySlug, getWindowStart } from '../utils/cycle';

const BACKEND = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';
const STORAGE_KEY = 'btc_markov_tracker_v1';
const RECONCILE_INTERVAL_MS = 12_000;
const POLY_GRACE_SECONDS = 30;
const POLY_TIMEOUT_SECONDS = 720; // 12 minutes before falling back to local

export type PredictionResult = 'WIN' | 'LOSS' | 'EXPIRED' | 'PENDING';
export type ResultSource = 'poly' | 'local';

export interface TrackerEntry {
  id: string;
  tf: TimeFrame;
  windowStart: number;
  direction: 'UP' | 'DOWN';
  probUp: number;
  probDown: number;
  sampleSize: number;
  confidence: string;
  priceAtOpen: number;
  recordedAt: number;
  result: PredictionResult;
  resultSource?: ResultSource;
  priceAtClose?: number;
}

export interface TFStats {
  total: number;
  wins: number;
  losses: number;
  winRate: number;
  recent: TrackerEntry[];
}

export interface TrackerState {
  entries: TrackerEntry[];
  stats: Record<TimeFrame, TFStats>;
  recordPrediction: (params: {
    tf: TimeFrame;
    windowStart: number;
    direction: 'UP' | 'DOWN';
    probUp: number;
    probDown: number;
    sampleSize: number;
    confidence: string;
    priceAtOpen: number;
  }) => void;
  updateClosePrice: (tf: TimeFrame, windowStart: number, priceAtClose: number) => void;
  clearHistory: () => void;
}

function computeStats(entries: TrackerEntry[]): Record<TimeFrame, TFStats> {
  const tfs: TimeFrame[] = ['5M', '15M', '1H'];
  const result = {} as Record<TimeFrame, TFStats>;
  for (const tf of tfs) {
    const tfEntries = entries.filter((e) => e.tf === tf);
    const scored = tfEntries.filter((e) => e.result === 'WIN' || e.result === 'LOSS');
    const wins = scored.filter((e) => e.result === 'WIN').length;
    const losses = scored.filter((e) => e.result === 'LOSS').length;
    const total = wins + losses;
    result[tf] = {
      total,
      wins,
      losses,
      winRate: total > 0 ? wins / total : 0,
      // Show last 20 for the dot display, but stats are over ALL history
      recent: scored.slice(-20),
    };
  }
  return result;
}

async function loadEntries(): Promise<TrackerEntry[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as TrackerEntry[]) : [];
  } catch {
    return [];
  }
}

async function saveEntries(entries: TrackerEntry[]): Promise<void> {
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Storage write failed — not fatal
  }
}

async function fetchPolyResolution(
  slug: string
): Promise<{ resolved: boolean; winner: string | null }> {
  try {
    const res = await fetch(`${BACKEND}/api/poly_resolution?slug=${encodeURIComponent(slug)}`);
    if (!res.ok) return { resolved: false, winner: null };
    return await res.json();
  } catch {
    return { resolved: false, winner: null };
  }
}

/**
 * Background performance tracker.
 *
 * Honesty rules from the guide:
 * 1. Reconciliation, not instant capture — retry every tick until resolved.
 * 2. Tracks all 3 TFs in background regardless of displayed tab.
 * 3. Never invents predictions — only records what was actually seen.
 * 4. NO EDGE (45-55%) = nothing recorded.
 * 5. Anti-duplicate: checks windowStart before pushing.
 * 6. Unlimited history.
 * 7. Tracks source per result (poly vs local).
 * 8. Freshness check must also exist here (bug: screen had it, tracker didn't).
 */
export function useTracker(): TrackerState {
  const [entries, setEntries] = useState<TrackerEntry[]>([]);
  const entriesRef = useRef<TrackerEntry[]>([]);

  const updateEntries = (updated: TrackerEntry[]) => {
    entriesRef.current = updated;
    setEntries([...updated]);
    saveEntries(updated);
  };

  // Load persisted data on mount
  useEffect(() => {
    loadEntries().then((loaded) => {
      entriesRef.current = loaded;
      setEntries(loaded);
    });
  }, []);

  // Reconcile pending entries every 12 seconds
  useEffect(() => {
    const reconcile = async () => {
      const now = Math.floor(Date.now() / 1000);
      let changed = false;
      const updated = [...entriesRef.current];

      for (let i = 0; i < updated.length; i++) {
        const entry = updated[i];
        if (entry.result !== 'PENDING') continue;

        const cycle = CYCLE_SECONDS[entry.tf];
        const windowEnd = entry.windowStart + cycle;

        // Window not yet closed
        if (now < windowEnd) continue;

        // Waiting for grace period
        if (now < windowEnd + POLY_GRACE_SECONDS) continue;

        const slug = getPolySlug(entry.tf, entry.windowStart);

        if (slug) {
          // Try Polymarket resolution
          const poly = await fetchPolyResolution(slug);
          if (poly.resolved && poly.winner) {
            const winner = poly.winner.toLowerCase();
            const predicted = entry.direction.toLowerCase();
            const isWin =
              (predicted === 'up' && winner === 'up') ||
              (predicted === 'down' && winner === 'down');
            updated[i] = {
              ...entry,
              result: isWin ? 'WIN' : 'LOSS',
              resultSource: 'poly',
            };
            changed = true;
            continue;
          }

          // Timeout fallback: score locally after 12 minutes
          if (now > windowEnd + POLY_TIMEOUT_SECONDS) {
            if (entry.priceAtClose !== undefined) {
              const localWin =
                (entry.direction === 'UP' && entry.priceAtClose >= entry.priceAtOpen) ||
                (entry.direction === 'DOWN' && entry.priceAtClose < entry.priceAtOpen);
              updated[i] = {
                ...entry,
                result: localWin ? 'WIN' : 'LOSS',
                resultSource: 'local',
              };
            } else {
              // No price available — cannot score, mark EXPIRED
              // Check if candle has left the history (too old to score)
              const ageMinutes = (now - windowEnd) / 60;
              if (ageMinutes > 30) {
                updated[i] = { ...entry, result: 'EXPIRED' };
              }
            }
            changed = true;
          }
        } else {
          // 1H: no Polymarket market — score locally
          if (now > windowEnd + POLY_GRACE_SECONDS) {
            if (entry.priceAtClose !== undefined) {
              const localWin =
                (entry.direction === 'UP' && entry.priceAtClose >= entry.priceAtOpen) ||
                (entry.direction === 'DOWN' && entry.priceAtClose < entry.priceAtOpen);
              updated[i] = {
                ...entry,
                result: localWin ? 'WIN' : 'LOSS',
                resultSource: 'local',
              };
              changed = true;
            }
          }
        }
      }

      if (changed) updateEntries(updated);
    };

    const interval = setInterval(reconcile, RECONCILE_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  const recordPrediction = useCallback(
    (params: {
      tf: TimeFrame;
      windowStart: number;
      direction: 'UP' | 'DOWN';
      probUp: number;
      probDown: number;
      sampleSize: number;
      confidence: string;
      priceAtOpen: number;
    }) => {
      // Anti-duplicate: never record the same window twice
      const existing = entriesRef.current.find(
        (e) => e.tf === params.tf && e.windowStart === params.windowStart
      );
      if (existing) return;

      const entry: TrackerEntry = {
        id: `${params.tf}-${params.windowStart}`,
        ...params,
        recordedAt: Date.now(),
        result: 'PENDING',
      };

      const updated = [...entriesRef.current, entry];
      updateEntries(updated);
    },
    []
  );

  /**
   * Called by the main screen to provide close prices for local scoring.
   * The tracker hook doesn't access live price directly — the screen
   * passes it when a window closes.
   */
  const updateClosePrice = useCallback(
    (tf: TimeFrame, windowStart: number, priceAtClose: number) => {
      const idx = entriesRef.current.findIndex(
        (e) => e.tf === tf && e.windowStart === windowStart && e.result === 'PENDING'
      );
      if (idx === -1) return;
      const updated = [...entriesRef.current];
      updated[idx] = { ...updated[idx], priceAtClose };
      updateEntries(updated);
    },
    []
  );

  const clearHistory = useCallback(() => {
    updateEntries([]);
  }, []);

  const stats = computeStats(entries);

  return { entries, stats, recordPrediction, updateClosePrice, clearHistory };
}
