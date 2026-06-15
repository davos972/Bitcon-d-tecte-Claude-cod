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
import { TimeFrame, CYCLE_SECONDS, getPhaseInfo, getWindowStart } from './utils/cycle';

const TIMEFRAMES: TimeFrame[] = ['15M', '5M', '1H'];

export default function App() {
  const [activeTab, setActiveTab] = useState<TimeFrame>('15M');
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));

  // Live price
  const priceState = usePrice();

  // Markov predictions — all 3 TFs always running in background (guide rule #2)
  const markov5M = useMarkov('5M');
  const markov15M = useMarkov('15M');
  const markov1H = useMarkov('1H');
  const markovByTF: Record<TimeFrame, ReturnType<typeof useMarkov>> = {
    '5M': markov5M,
    '15M': markov15M,
    '1H': markov1H,
  };
  const activeMarkov = markovByTF[activeTab];

  // Performance tracker
  const tracker = useTracker();

  // Clock: tick every second
  useEffect(() => {
    const interval = setInterval(() => {
      setNow(Math.floor(Date.now() / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // Phase info for the active tab only (display purposes)
  const { phase, windowStart, remaining, progress } = getPhaseInfo(now, activeTab);

  // -------------------------------------------------------------------------
  // Price to Beat — 3 rules from the guide
  // Rule 1: = open of the CURRENT window, never the price when user opens app
  // Rule 2: recaptured ONLY when windowStart changes (not on every render)
  // Rule 3: snapshot live price immediately, then refine to exact candle open
  // -------------------------------------------------------------------------
  const [priceToBeat, setPriceToBeat] = useState<number | null>(null);
  const priceToBeatRef = useRef<number | null>(null);
  const windowOpenTsRef = useRef<number>(0);
  const priceInitializedRef = useRef(false);
  // windowStart for which we captured a Chainlink RTDS snapshot AT the boundary.
  // Polymarket resolves on the Chainlink BTC/USD stream, so when we have that
  // boundary snapshot it IS the price to beat — we must NOT overwrite it with
  // the Coinbase candle open (Rule 3b), which is a different source (~2-3$ off).
  const chainlinkAtBoundaryRef = useRef<number>(0);

  // Trigger: window boundary changed → capture new open price (Rule 2)
  useEffect(() => {
    if (windowStart !== windowOpenTsRef.current) {
      windowOpenTsRef.current = windowStart;
      if (priceState.price !== null) {
        priceToBeatRef.current = priceState.price;
        setPriceToBeat(priceState.price);
        priceInitializedRef.current = true;
        // The Chainlink live snapshot only equals the window OPEN when we are
        // at the real-time boundary (the window just opened). On a timeframe
        // switch, windowStart also changes but points to a window opened
        // minutes ago — the live price is NOT its open, so we must let Rule 3b
        // refine to the candle open. Distinguish by how fresh the window is.
        const atRealBoundary = now - windowStart <= 2;
        chainlinkAtBoundaryRef.current =
          atRealBoundary && priceState.source === 'CHAINLINK_RTDS'
            ? windowStart
            : 0;
      }
    }
  }, [windowStart]);

  // Trigger: price arrives for the first time before first window boundary
  useEffect(() => {
    if (
      priceState.price !== null &&
      !priceInitializedRef.current &&
      windowOpenTsRef.current > 0
    ) {
      priceToBeatRef.current = priceState.price;
      setPriceToBeat(priceState.price);
      priceInitializedRef.current = true;
    }
  }, [priceState.price]);

  // Initialise windowOpenTsRef on mount so the price trigger above works
  useEffect(() => {
    const ws = getWindowStart(Math.floor(Date.now() / 1000), activeTab);
    if (windowOpenTsRef.current === 0) {
      windowOpenTsRef.current = ws;
    }
  }, []);

  // Rule 3b: refine to exact candle open once historical data has it.
  // Skipped when we already hold a Chainlink RTDS boundary snapshot for this
  // window — that snapshot matches Polymarket's resolution source; the Coinbase
  // candle open does not. Still applies on FALLBACK or mid-window app opens.
  useEffect(() => {
    const d = activeMarkov.data;
    if (
      d?.current_window_open != null &&
      activeMarkov.windowStart === windowOpenTsRef.current &&
      chainlinkAtBoundaryRef.current !== windowOpenTsRef.current
    ) {
      const exact = d.current_window_open;
      if (exact !== priceToBeatRef.current) {
        priceToBeatRef.current = exact;
        setPriceToBeat(exact);
      }
    }
  }, [activeMarkov.data?.current_window_open, activeMarkov.windowStart]);

  // -------------------------------------------------------------------------
  // Close price tracking — one ref per TF, detects window transitions
  // Used to provide local scoring data to the tracker (needed for 1H)
  // -------------------------------------------------------------------------
  const prevWindowsRef = useRef<Partial<Record<TimeFrame, number>>>({});

  useEffect(() => {
    for (const tf of TIMEFRAMES) {
      const ws = getWindowStart(now, tf);
      const prev = prevWindowsRef.current[tf];
      if (prev !== undefined && ws !== prev) {
        // The previous window just closed — pass close price to tracker
        if (priceState.price !== null) {
          tracker.updateClosePrice(tf, prev, priceState.price);
        }
      }
      prevWindowsRef.current[tf] = ws;
    }
  }, [now]);

  // -------------------------------------------------------------------------
  // Record predictions (once per window, during ANALYSIS, only if edge exists)
  // Freshness check is also required here — bug #9 fix from the guide
  // -------------------------------------------------------------------------
  const recordedRef = useRef<Partial<Record<TimeFrame, number>>>({});

  useEffect(() => {
    for (const tf of TIMEFRAMES) {
      const m = markovByTF[tf];
      const { windowStart: ws, phase: p } = getPhaseInfo(now, tf);

      if (p !== 'ANALYSIS') continue;
      if (m.status !== 'ok' || !m.data) continue;
      if (m.noEdge) continue;          // NO EDGE = nothing recorded (guide rule #4)
      if (!m.isFresh) continue;        // Stale prediction = never record (bug #9 fix)
      if (m.data.direction === 'NONE') continue;
      if (recordedRef.current[tf] === ws) continue; // Anti-duplicate (rule #5)
      if (!priceState.price) continue;

      // Prefer the exact candle open of this window (used for local scoring of
      // the 1H tab) over the live price, which is captured a few seconds into
      // the window. Only trust it when the Markov data is for this same window.
      const exactOpen =
        m.windowStart === ws && m.data.current_window_open != null
          ? m.data.current_window_open
          : priceState.price;

      recordedRef.current[tf] = ws;
      tracker.recordPrediction({
        tf,
        windowStart: ws,
        direction: m.data.direction as 'UP' | 'DOWN',
        probUp: m.data.prob_up,
        probDown: m.data.prob_down,
        sampleSize: m.data.sample_size,
        confidence: m.data.confidence,
        priceAtOpen: exactOpen,
      });
    }
  }, [now, markov5M.status, markov15M.status, markov1H.status]);

  // -------------------------------------------------------------------------
  // hasData: must check candle_count > 0, NOT just object existence (bug fix)
  // Showing 50/50 when no data is dangerous — user could bet on it
  // -------------------------------------------------------------------------
  const hasData =
    activeMarkov.data !== null &&
    activeMarkov.data.candle_count > 0 &&
    activeMarkov.status !== 'error';

  const showNoEdge = hasData && activeMarkov.noEdge;

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="light" />
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>

        {/* 1. Top bar: live price + data source indicator */}
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

        {/* 3. Timeframe tabs — 15M is default (best documented edge) */}
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

        {/* 4. Cycle timer with phase + countdown + progress bar */}
        <CycleTimer phase={phase} remaining={remaining} progress={progress} />

        {/* 5. UP/DOWN nodes — only one is "lit", UPDATING during recalculation */}
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
            <Text style={styles.noEdgeText}>
              NO EDGE — SKIP · {Math.round((activeMarkov.data?.prob_up ?? 0.5) * 100)}% vs {Math.round((activeMarkov.data?.prob_down ?? 0.5) * 100)}%
            </Text>
          </View>
        )}

        {/* 7. Permanent disclaimer */}
        <View style={styles.disclaimer}>
          <Text style={styles.disclaimerText}>
            Short-timeframe candles are highly random. Probabilities near 50%
            mean no edge. Past patterns do not guarantee future results.
          </Text>
        </View>

        {/* 8. Markov pattern panel */}
        <MarkovPanel data={activeMarkov.data} hasData={hasData} />

        {/* 9. Candle strip — last 20 closed candles */}
        <CandleStrip
          recentCandles={activeMarkov.data?.recent_candles ?? []}
          candleCount={activeMarkov.data?.candle_count ?? 0}
        />

        {/* 10. Performance tracker */}
        <PerformancePanel stats={tracker.stats} activeTab={activeTab} />

        <TouchableOpacity style={styles.clearBtn} onPress={tracker.clearHistory}>
          <Text style={styles.clearText}>Reset history</Text>
        </TouchableOpacity>

        <View style={{ height: 50 }} />
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
  tab: { flex: 1, paddingVertical: 8, alignItems: 'center', borderRadius: 6 },
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
  disclaimerText: {
    fontSize: 11,
    color: '#666',
    lineHeight: 16,
    textAlign: 'center',
  },

  clearBtn: { marginHorizontal: 16, marginTop: 24, alignItems: 'center', opacity: 0.35 },
  clearText: { fontSize: 11, color: '#888' },
});
