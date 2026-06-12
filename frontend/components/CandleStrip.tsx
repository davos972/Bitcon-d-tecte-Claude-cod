import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

interface Props {
  pattern: string[];
  candleCount: number;
}

export function CandleStrip({ pattern, candleCount }: Props) {
  // Show the pattern candles as colored squares
  const display = pattern.slice(-20);

  return (
    <View style={styles.container}>
      <View style={styles.row}>
        {display.map((p, i) => (
          <View
            key={i}
            style={[styles.candle, p === 'U' ? styles.up : styles.down]}
          />
        ))}
      </View>
      <Text style={styles.counter}>
        {candleCount > 0 ? `${candleCount} candles in history` : 'Loading…'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginHorizontal: 16, marginTop: 8 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 3, marginBottom: 4 },
  candle: { width: 12, height: 16, borderRadius: 2 },
  up: { backgroundColor: '#00C853' },
  down: { backgroundColor: '#FF3D00' },
  counter: { fontSize: 10, color: '#555' },
});
