import React, { useEffect, useRef, useState } from 'react';
import {
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';

import { PriceBar } from './components/PriceBar';
import { PriceToBeat } from './components/PriceToBeat';
import { CycleTimer } from './components/CycleTimer';
import { UpDownNodes } from './components/UpDownNodes';
import { MarkovPanel } from './components/MarkovPanel';
import { CandleStrip } from './components/CandleStrip';
import { PerformancePanel } from './components/PerformancePanel';

import { usePrice } from './hooks/usePrice';
import { useMarkov } from './hooks/useMarkov';
import { useTracker } from './hooks/useTracker';
import { TimeFrame, getPhaseInfo } from './utils/cycle';

const TIMEFRAMES: TimeFrame[] = ['15M', '5M', '1H'];

export default function App() {
  const [activeTab, setActiveTab] = useState<TimeFrame>('15M');
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));

  // Live price
  const priceState = usePrice();

  // Markov predictions for all 3 TFs (background tracking)
  const markov5M = useMarkov('5M');
  const markov15M = useMarkov('15M');
  const markov1H = useMarkov('1H');
  const markovByTF = { '5M': markov5M, '15M': markov15M, '1H': markov1H };
  const activeMarkov = markovByTF[activeTab];

  // Performance tracker
  const tracker = useTracker();

  // Clock tick every second
  useEffect(() => {
    const interval = setInterval(() => {
      setNow(Math.floor(Date.now() / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // Phase info for active tab
  const { phase, windowStart, elapsed, remaining, progress } = getPhaseInfo(now, activeTab);

  // --- Price to Beat (3 rules from guide) ---
  // Rule 1: = open of CURRENT window, never arrival price
  // Rule 2: recaptured ONLY when windowStart changes
  // Rule 3: snapshot live price immediately, refine to candle open when available
  const priceToBeatRef = useRef<number | null>(null);
  const windowOpenTsRef = useRef<number>(0);
  const [priceToBeat, setPriceToBeat] = useState<number | null>(null);

  useEffect(() => {
    if (windowStart !== windowOpenTsRef.current) {
      windowOpenTsRef.current = windowStart;
      // Snapshot live price immediately (Rule 3a)
      if (priceState.price !== null) {
        priceToBeatRef.current = priceState.price;
        setPriceToBeat(priceState.price);
      }
    }
  }, [windowStart]); // Only triggers on window boundary change (Rule 2)

  // Refine Price to Beat with exact candle open once it appears in history (Rule 3b)
  useEffect(() => {
    if (
      activeMarkov.data?.current_window_open !== null &&
      activeMarkov.data?.current_window_open !== undefined &&
      windowOpenTsRef.current === activeMarkov.windowStart
    ) {
      const exactOpen = activeMarkov.data.current_window_open;
      if (exactOpen !== priceToBeatRef.current) {
        priceToBeatRef.current = exactOpen;
        setPriceToBeat(exactOpen);
      }
    }
  }, [activeMarkov.data?.current_window_open, activeMarkov.windowStart]);

  // --- Record predictions ---
  // For each TF, record a prediction during ANALYSIS phase if we have edge
  const recordedRef = useRef<Partial<Record<TimeFrame, number>>>({});

  useEffect(() => {
    const recordForTF = (tf: TimeFrame) => {
      const m = markovByTF[tf];
      const { windowStart: ws, phase: p } = getPhaseInfo(now, tf);

      if (p !== 'ANALYSIS') return;
      if (m.status !== 'ok' || !m.data || m.noEdge) return;
      if (!m.isFresh) return; // Never record stale predictions (bug #9 fix)
      if (m.data.direction === 'NONE') return;
      if (recordedRef.current[tf] === ws) return; // Anti-duplicate

      const price = priceState.price;
      if (!price) return;

      recordedRef.current[tf] = ws;
      tracker.recordPrediction({
        tf,
        windowStart: ws,
        direction: m.data.direction as 'UP' | 'DOWN',
        probUp: m.data.prob_up,
        probDown: m.data.prob_down,
        sampleSize: m.data.sample_size,
        confidence: m.data.confidence,
        priceAtOpen: price,
      });
    };

    for (const tf of TIMEFRAMES) {
      recordForTF(tf);
    }
  }, [now, markov5M.status, markov15M.status, markov1H.status]);

  // Data validity: hasData must check candle_count > 0, not just object existence (bug fix)
  const hasData =
    activeMarkov.data !== null &&
    activeMarkov.data.candle_count > 0 &&
    activeMarkov.status !== 'error';

  // NO EDGE banner
  const showNoEdge = hasData && activeMarkov.noEdge;

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="light" />
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>

        {/* 1. Top bar: live price + source indicator */}
        <PriceBar
          price={priceState.price}
          source={priceState.source}
          isFallback={priceState.isFallback}
        />

        {/* 2. Price to Beat */}
        <PriceToBeat
          priceToBeat={priceToBeat}
          currentPrice={priceState.price}
        />

        {/* 3. Timeframe tabs */}
        <View style={styles.tabs}>
          {TIMEFRAMES.map((tf) => (
            <TouchableOpacity
              key={tf}
              style={[styles.tab, activeTab === tf && styles.tabActive]}
              onPress={() => setActiveTab(tf)}
            >
              <Text style={[styles.tabText, activeTab === tf && styles.tabTextActive]}>
                {tf}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* 4. Cycle timer */}
        <CycleTimer phase={phase} remaining={remaining} progress={progress} />

        {/* 5. UP/DOWN nodes */}
        <UpDownNodes
          probUp={activeMarkov.data?.prob_up ?? 0.5}
          probDown={activeMarkov.data?.prob_down ?? 0.5}
          direction={activeMarkov.data?.direction ?? 'NONE'}
          status={activeMarkov.status}
          noEdge={showNoEdge}
          hasData={hasData}
        />

        {/* 6. NO EDGE banner */}
        {showNoEdge && (
          <View style={styles.noEdgeBanner}>
            <Text style={styles.noEdgeText}>NO EDGE — SKIP · {Math.round((activeMarkov.data?.prob_up ?? 0.5) * 100)}% UP</Text>
          </View>
        )}

        {/* 7. Disclaimer (permanent) */}
        <View style={styles.disclaimer}>
          <Text style={styles.disclaimerText}>
            Short-timeframe candles are highly random. Probabilities near 50%
            mean no edge. Past patterns do not guarantee future results.
          </Text>
        </View>

        {/* 8. Markov pattern panel */}
        <MarkovPanel data={activeMarkov.data} hasData={hasData} />

        {/* 9. Candle strip */}
        <CandleStrip
          pattern={activeMarkov.data?.pattern ?? []}
          candleCount={activeMarkov.data?.candle_count ?? 0}
        />

        {/* 10. Performance */}
        <PerformancePanel stats={tracker.stats} activeTab={activeTab} />

        {/* Clear history (dev utility) */}
        <TouchableOpacity style={styles.clearBtn} onPress={tracker.clearHistory}>
          <Text style={styles.clearText}>Clear history</Text>
        </TouchableOpacity>

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0A0A0A' },
  scroll: { flex: 1 },
  content: { paddingBottom: 20 },

  tabs: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginTop: 12,
    backgroundColor: '#111',
    borderRadius: 8,
    padding: 3,
  },
  tab: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    borderRadius: 6,
  },
  tabActive: { backgroundColor: '#1a1a4a' },
  tabText: { fontSize: 13, fontWeight: '600', color: '#555' },
  tabTextActive: { color: '#7986CB' },

  noEdgeBanner: {
    marginHorizontal: 16,
    marginTop: 10,
    backgroundColor: '#2a2a00',
    borderRadius: 6,
    padding: 10,
    alignItems: 'center',
  },
  noEdgeText: { color: '#FFD600', fontWeight: '700', fontSize: 14 },

  disclaimer: {
    marginHorizontal: 16,
    marginTop: 10,
    borderWidth: 1,
    borderColor: '#222',
    borderRadius: 6,
    padding: 8,
  },
  disclaimerText: { fontSize: 11, color: '#666', lineHeight: 16, textAlign: 'center' },

  clearBtn: { marginHorizontal: 16, marginTop: 20, alignItems: 'center', opacity: 0.4 },
  clearText: { fontSize: 11, color: '#888' },
});
