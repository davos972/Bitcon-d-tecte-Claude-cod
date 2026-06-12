import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

interface Props {
  recentCandles: string[];
  candleCount: number;
}

export function CandleStrip({ recentCandles, candleCount }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>RECENT CANDLES</Text>
      <View style={styles.row}>
        {recentCandles.map((d, i) => (
          <View key={i} style={[styles.candle, d === 'U' ? styles.up : styles.down]} />
        ))}
        {recentCandles.length === 0 && (
          <Text style={styles.empty}>Loading…</Text>
        )}
      </View>
      <Text style={styles.counter}>
        {candleCount > 0 ? `${candleCount} candles loaded` : '—'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginHorizontal: 16, marginTop: 10 },
  label: { fontSize: 10, color: '#555', letterSpacing: 1, marginBottom: 6 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 3, marginBottom: 4 },
  candle: { width: 13, height: 18, borderRadius: 2 },
  up: { backgroundColor: '#00C853' },
  down: { backgroundColor: '#FF3D00' },
  empty: { fontSize: 11, color: '#555', fontStyle: 'italic' },
  counter: { fontSize: 10, color: '#444' },
});
