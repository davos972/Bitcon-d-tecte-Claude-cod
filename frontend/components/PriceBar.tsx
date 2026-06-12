import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { PriceSource } from '../hooks/usePrice';

interface Props {
  price: number | null;
  source: PriceSource;
  isFallback: boolean;
}

export function PriceBar({ price, source, isFallback }: Props) {
  const displayPrice = price !== null ? `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

  return (
    <View style={styles.container}>
      <View style={styles.row}>
        <Text style={styles.price}>{displayPrice}</Text>
        <View style={[styles.sourceBadge, isFallback ? styles.fallback : styles.rtds]}>
          <Text style={styles.sourceText}>
            {isFallback ? 'FALLBACK' : 'CHAINLINK RTDS'}
          </Text>
        </View>
      </View>
      {isFallback && (
        <View style={styles.warningBanner}>
          <Text style={styles.warningText}>
            ⚠ Price source: CryptoCompare/Coinbase — not the Chainlink feed Polymarket uses to resolve
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { paddingHorizontal: 16, paddingTop: 12 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  price: { fontSize: 28, fontWeight: '700', color: '#FFFFFF', fontVariant: ['tabular-nums'] },
  sourceBadge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  rtds: { backgroundColor: '#1a3a1a' },
  fallback: { backgroundColor: '#3a2a00' },
  sourceText: { fontSize: 10, fontWeight: '600', letterSpacing: 0.5 },
  warningBanner: { marginTop: 6, backgroundColor: '#3a2a00', borderRadius: 4, padding: 6 },
  warningText: { fontSize: 11, color: '#FFA500' },
});
