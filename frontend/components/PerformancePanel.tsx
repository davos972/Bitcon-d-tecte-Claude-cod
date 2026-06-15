import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { TFStats } from '../hooks/useTracker';
import { TimeFrame } from '../utils/cycle';

interface Props {
  stats: Record<TimeFrame, TFStats>;
  activeTab: TimeFrame;
}

export function PerformancePanel({ stats, activeTab }: Props) {
  const tfs: TimeFrame[] = ['15M', '5M', '1H'];

  // Find best win rate among tabs with enough data
  const bestTF = tfs.reduce<TimeFrame | null>((best, tf) => {
    if (stats[tf].total < 10) return best;
    if (!best) return tf;
    return stats[tf].winRate > stats[best].winRate ? tf : best;
  }, null);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>PERFORMANCE</Text>
      {tfs.map((tf) => (
        <TFRow
          key={tf}
          tf={tf}
          s={stats[tf]}
          isBest={tf === bestTF}
          isLocal={tf === '1H'}
        />
      ))}

      {/* Dot display for active tab */}
      <View style={styles.dotsSection}>
        <Text style={styles.dotsLabel}>Last {Math.min(20, stats[activeTab].recent.length)} results ({activeTab})</Text>
        <View style={styles.dotsRow}>
          {stats[activeTab].recent.map((e, i) => (
            <View
              key={i}
              style={[
                styles.dot,
                e.result === 'WIN' ? styles.dotWin : styles.dotLoss,
              ]}
            />
          ))}
          {stats[activeTab].recent.length === 0 && (
            <Text style={styles.noData}>No scored predictions yet</Text>
          )}
        </View>
      </View>

      {/* Interpretation guide */}
      <View style={styles.guide}>
        <Text style={styles.guideText}>
          {'< 30 predictions → ignore (noise)\n'}
          {'100+ at ~50% → no edge, do not trade\n'}
          {'100+ at 53-54% → fragile, spread will eat it\n'}
          {'100+ at >55% → potentially real'}
        </Text>
      </View>
    </View>
  );
}

function TFRow({
  tf,
  s,
  isBest,
  isLocal,
}: {
  tf: TimeFrame;
  s: TFStats;
  isBest: boolean;
  isLocal: boolean;
}) {
  const pct = s.total > 0 ? Math.round(s.winRate * 100) : 0;
  const barWidth = `${pct}%` as any;
  const barColor = pct >= 55 ? '#00C853' : pct >= 50 ? '#FFA500' : '#FF3D00';

  return (
    <View style={styles.tfRow}>
      <View style={styles.tfLeft}>
        <Text style={styles.tfLabel}>
          {isBest ? '★ ' : ''}{tf}{isLocal ? ' ·local' : ''}
        </Text>
        <Text style={styles.tfScore}>
          {s.wins}/{s.total}
        </Text>
      </View>
      <View style={styles.barContainer}>
        <View style={styles.barTrack}>
          <View style={[styles.barFill, { width: barWidth, backgroundColor: barColor }]} />
        </View>
        <Text style={[styles.pct, { color: s.total >= 30 ? barColor : '#666' }]}>
          {s.total > 0 ? `${pct}%` : '—'}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginHorizontal: 16, marginTop: 12, backgroundColor: '#111', borderRadius: 10, padding: 12 },
  title: { fontSize: 10, color: '#666', letterSpacing: 1, marginBottom: 8 },
  tfRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 8, gap: 12 },
  tfLeft: { width: 60 },
  tfLabel: { fontSize: 12, fontWeight: '700', color: '#CCC' },
  tfScore: { fontSize: 10, color: '#666' },
  barContainer: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 },
  barTrack: { flex: 1, height: 6, backgroundColor: '#222', borderRadius: 3, overflow: 'hidden' },
  barFill: { height: 6, borderRadius: 3 },
  pct: { width: 32, fontSize: 11, fontWeight: '600', textAlign: 'right', fontVariant: ['tabular-nums'] },
  dotsSection: { marginTop: 8, borderTopWidth: 1, borderTopColor: '#222', paddingTop: 8 },
  dotsLabel: { fontSize: 10, color: '#555', marginBottom: 6 },
  dotsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  dotWin: { backgroundColor: '#00C853' },
  dotLoss: { backgroundColor: '#FF3D00' },
  noData: { fontSize: 11, color: '#555', fontStyle: 'italic' },
  guide: { marginTop: 10, borderTopWidth: 1, borderTopColor: '#222', paddingTop: 8 },
  guideText: { fontSize: 10, color: '#555', lineHeight: 16 },
});
