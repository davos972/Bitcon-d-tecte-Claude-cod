import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { TimeFrame } from '../utils/cycle';

const BACKEND = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';

interface BacktestResult {
  mode: string;
  bets: number;
  wins: number;
  win_rate: number;
  ci95_low: number;
  ci95_high: number;
  edge_confirmed: boolean;
  candle_count: number;
}

/**
 * Walk-forward backtest panel. Replays the same Markov engine over the whole
 * candle history (predicting each candle from only prior candles) to give a
 * large, out-of-sample estimate of the edge in one shot — instead of waiting
 * weeks for live results. Idealised upper bound (see caveat).
 */
export function BacktestPanel({ activeTab }: { activeTab: TimeFrame }) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Clear stale result when the timeframe changes.
  useEffect(() => {
    setData(null);
    setError(null);
  }, [activeTab]);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${BACKEND}/api/backtest?mode=${activeTab}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData((await res.json()) as BacktestResult);
    } catch {
      setError('Backtest failed — check connection');
    } finally {
      setLoading(false);
    }
  };

  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>BACKTEST (walk-forward)</Text>
        <TouchableOpacity style={styles.btn} onPress={run} disabled={loading}>
          {loading ? (
            <ActivityIndicator size="small" color="#7986CB" />
          ) : (
            <Text style={styles.btnText}>Run · {activeTab}</Text>
          )}
        </TouchableOpacity>
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      {data && (
        <View>
          <View style={styles.row}>
            <Text style={styles.winRate}>{pct(data.win_rate)}</Text>
            <View style={styles.detail}>
              <Text style={styles.detailText}>
                {data.wins}/{data.bets} paris simulés · {data.candle_count} bougies
              </Text>
              <Text style={styles.detailText}>
                IC 95 % : {pct(data.ci95_low)} – {pct(data.ci95_high)}
              </Text>
              <Text
                style={[
                  styles.edge,
                  { color: data.edge_confirmed ? '#00C853' : '#FF7043' },
                ]}
              >
                {data.edge_confirmed
                  ? '✓ borne basse > 50 % (edge significatif)'
                  : '✗ borne basse ≤ 50 % (pas d’edge prouvé)'}
              </Text>
            </View>
          </View>
          <Text style={styles.caveat}>
            Plafond idéalisé : ignore le timing live et la résolution Polymarket.
            Le live sera un peu en dessous.
          </Text>
        </View>
      )}

      {!data && !error && !loading && (
        <Text style={styles.hint}>
          Rejoue le moteur sur tout l’historique → estimation de l’edge sur des
          centaines de paris, tout de suite.
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginHorizontal: 16, marginTop: 12, backgroundColor: '#111', borderRadius: 10, padding: 12 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: 10, color: '#666', letterSpacing: 1 },
  btn: { backgroundColor: '#1a1a4a', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 6, minWidth: 74, alignItems: 'center' },
  btnText: { color: '#7986CB', fontSize: 12, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', marginTop: 10, gap: 12 },
  winRate: { fontSize: 30, fontWeight: '800', color: '#EEE', fontVariant: ['tabular-nums'] },
  detail: { flex: 1 },
  detailText: { fontSize: 11, color: '#999', lineHeight: 16 },
  edge: { fontSize: 11, fontWeight: '700', marginTop: 2 },
  caveat: { fontSize: 10, color: '#555', marginTop: 8, lineHeight: 14 },
  hint: { fontSize: 11, color: '#666', marginTop: 8, lineHeight: 15 },
  error: { fontSize: 12, color: '#FF7043', marginTop: 8 },
});
