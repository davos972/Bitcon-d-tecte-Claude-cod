import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

interface Props {
  priceToBeat: number | null;
  currentPrice: number | null;
}

export function PriceToBeat({ priceToBeat, currentPrice }: Props) {
  if (!priceToBeat) {
    return (
      <View style={styles.container}>
        <Text style={styles.label}>PRICE TO BEAT</Text>
        <Text style={styles.value}>Loading…</Text>
      </View>
    );
  }

  const delta = currentPrice !== null ? currentPrice - priceToBeat : 0;
  const isUp = delta >= 0;
  const deltaColor = isUp ? '#00C853' : '#FF3D00';

  return (
    <View style={styles.container}>
      <Text style={styles.label}>PRICE TO BEAT</Text>
      <View style={styles.row}>
        <Text style={styles.value}>
          ${priceToBeat.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </Text>
        {currentPrice !== null && (
          <Text style={[styles.delta, { color: deltaColor }]}>
            {isUp ? '+' : ''}${delta.toFixed(2)}
          </Text>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { paddingHorizontal: 16, paddingVertical: 10, backgroundColor: '#111', marginHorizontal: 16, borderRadius: 8, marginTop: 8 },
  label: { fontSize: 10, color: '#888', letterSpacing: 1, marginBottom: 4 },
  row: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  value: { fontSize: 20, fontWeight: '700', color: '#FFFFFF', fontVariant: ['tabular-nums'] },
  delta: { fontSize: 16, fontWeight: '600', fontVariant: ['tabular-nums'] },
});
