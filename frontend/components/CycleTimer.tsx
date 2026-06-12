import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Phase, formatCountdown } from '../utils/cycle';

interface Props {
  phase: Phase;
  remaining: number;
  progress: number;
}

const PHASE_COLORS: Record<Phase, string> = {
  ANALYSIS: '#1565C0',
  LOCK: '#E65100',
  RESOLUTION: '#1B5E20',
};

const PHASE_LABELS: Record<Phase, string> = {
  ANALYSIS: 'ANALYSIS',
  LOCK: 'LOCK — BET NOW',
  RESOLUTION: 'RESOLVING…',
};

export function CycleTimer({ phase, remaining, progress }: Props) {
  const color = PHASE_COLORS[phase];

  return (
    <View style={styles.container}>
      <View style={styles.row}>
        <View style={[styles.phaseBadge, { backgroundColor: color }]}>
          <Text style={styles.phaseText}>{PHASE_LABELS[phase]}</Text>
        </View>
        <Text style={styles.countdown}>{formatCountdown(remaining)}</Text>
      </View>
      {/* Progress bar */}
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${progress * 100}%` as any, backgroundColor: color }]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { paddingHorizontal: 16, marginTop: 8 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 },
  phaseBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 4 },
  phaseText: { fontSize: 11, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
  countdown: { fontSize: 22, fontWeight: '700', color: '#FFF', fontVariant: ['tabular-nums'] },
  barTrack: { height: 4, backgroundColor: '#333', borderRadius: 2, overflow: 'hidden' },
  barFill: { height: 4, borderRadius: 2 },
});
