import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { MarkovStatus } from '../hooks/useMarkov';

interface Props {
  probUp: number;
  probDown: number;
  direction: 'UP' | 'DOWN' | 'NONE';
  status: MarkovStatus;
  noEdge: boolean;
  hasData: boolean;
}

export function UpDownNodes({ probUp, probDown, direction, status, noEdge, hasData }: Props) {
  const isUpdating = status === 'updating' || status === 'loading';
  const isGrayed = noEdge || !hasData;

  const upActive = !isGrayed && direction === 'UP';
  const downActive = !isGrayed && direction === 'DOWN';

  return (
    <View style={styles.row}>
      <NodeBox
        label="UP"
        percent={Math.round(probUp * 100)}
        active={upActive}
        grayed={isGrayed}
        updating={isUpdating}
        color="#00C853"
      />
      <NodeBox
        label="DOWN"
        percent={Math.round(probDown * 100)}
        active={downActive}
        grayed={isGrayed}
        updating={isUpdating}
        color="#FF3D00"
      />
    </View>
  );
}

function NodeBox({
  label,
  percent,
  active,
  grayed,
  updating,
  color,
}: {
  label: string;
  percent: number;
  active: boolean;
  grayed: boolean;
  updating: boolean;
  color: string;
}) {
  const displayColor = grayed ? '#555' : color;
  const borderColor = active ? color : grayed ? '#333' : '#444';

  return (
    <View
      style={[
        styles.node,
        { borderColor },
        active && { shadowColor: color, shadowOpacity: 0.8, shadowRadius: 12, elevation: 8 },
      ]}
    >
      <Text style={[styles.nodeLabel, { color: displayColor }]}>{label}</Text>
      {updating ? (
        <Text style={styles.updating}>UPDATING…</Text>
      ) : (
        <Text style={[styles.percent, { color: active ? color : grayed ? '#555' : '#AAA' }]}>
          {percent}%
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', paddingHorizontal: 16, gap: 12, marginTop: 12 },
  node: {
    flex: 1,
    borderWidth: 2,
    borderRadius: 12,
    paddingVertical: 20,
    alignItems: 'center',
    backgroundColor: '#0D0D0D',
  },
  nodeLabel: { fontSize: 14, fontWeight: '700', letterSpacing: 1, marginBottom: 6 },
  percent: { fontSize: 36, fontWeight: '800', fontVariant: ['tabular-nums'] },
  updating: { fontSize: 14, color: '#888', fontStyle: 'italic' },
});
