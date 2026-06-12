import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { MarkovData } from '../hooks/useMarkov';

interface Props {
  data: MarkovData | null;
  hasData: boolean;
}

export function MarkovPanel({ data, hasData }: Props) {
  if (!hasData || !data) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>
          No historical data loaded — check connection
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Pattern badges */}
      <View style={styles.patternRow}>
        {data.pattern.map((p, i) => (
          <View
            key={i}
            style={[styles.badge, p === 'U' ? styles.badgeUp : styles.badgeDown]}
          >
            <Text style={styles.badgeText}>{p}</Text>
          </View>
        ))}
        <Text style={styles.arrow}>→</Text>
        <View style={styles.badge}>
          <Text style={styles.badgeText}>?</Text>
        </View>
      </View>

      {/* Stats row */}
      <View style={styles.statsRow}>
        <StatChip label="N" value={String(data.n_used)} />
        <StatChip label="Sample" value={String(data.sample_size)} />
        <StatChip
          label="Confidence"
          value={data.confidence}
          color={
            data.confidence === 'HIGH'
              ? '#00C853'
              : data.confidence === 'MEDIUM'
              ? '#FFA500'
              : '#FF3D00'
          }
        />
      </View>

      {/* Historical result */}
      <Text style={styles.historyText}>
        After this pattern historically:{' '}
        <Text style={styles.upText}>UP {data.up_count} ({Math.round(data.prob_up * 100)}%)</Text>
        {'  /  '}
        <Text style={styles.downText}>DOWN {data.down_count} ({Math.round(data.prob_down * 100)}%)</Text>
      </Text>

      <Text style={styles.metaText}>
        {data.candle_count} candles loaded · source: {data.candle_source}
      </Text>
    </View>
  );
}

function StatChip({ label, value, color = '#AAA' }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.chip}>
      <Text style={styles.chipLabel}>{label}</Text>
      <Text style={[styles.chipValue, { color }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginHorizontal: 16, marginTop: 12, backgroundColor: '#111', borderRadius: 10, padding: 12 },
  errorText: { color: '#FF5252', fontSize: 13, textAlign: 'center' },
  patternRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  badge: { width: 28, height: 28, borderRadius: 6, backgroundColor: '#333', alignItems: 'center', justifyContent: 'center' },
  badgeUp: { backgroundColor: '#1a3a1a' },
  badgeDown: { backgroundColor: '#3a1a1a' },
  badgeText: { fontSize: 13, fontWeight: '700', color: '#FFF' },
  arrow: { fontSize: 16, color: '#666', marginHorizontal: 4 },
  statsRow: { flexDirection: 'row', gap: 8, marginBottom: 10 },
  chip: { flexDirection: 'row', gap: 4, backgroundColor: '#1a1a1a', borderRadius: 4, paddingHorizontal: 8, paddingVertical: 4 },
  chipLabel: { fontSize: 11, color: '#666' },
  chipValue: { fontSize: 11, fontWeight: '600' },
  historyText: { fontSize: 13, color: '#CCC', lineHeight: 20 },
  upText: { color: '#00C853', fontWeight: '600' },
  downText: { color: '#FF3D00', fontWeight: '600' },
  metaText: { fontSize: 10, color: '#555', marginTop: 6 },
});
