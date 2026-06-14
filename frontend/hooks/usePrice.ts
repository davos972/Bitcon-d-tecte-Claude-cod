import { useEffect, useRef, useState } from 'react';

const BACKEND = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';
const POLL_INTERVAL_MS = 5000;
// Correct host is ws-live-data (with hyphens). The old 'ws-livedata' never
// resolved → the app was permanently stuck on the FALLBACK price source
// instead of the Chainlink feed Polymarket actually resolves markets with.
const WS_URL = 'wss://ws-live-data.polymarket.com';
const RTDS_TOPIC = 'crypto_prices_chainlink';
const RTDS_ASSET = 'btc/usd';

export type PriceSource = 'CHAINLINK_RTDS' | 'FALLBACK';

export interface PriceState {
  price: number | null;
  source: PriceSource;
  isFallback: boolean;
  lastUpdated: number | null;
}

/**
 * Provides live BTC/USD price.
 *
 * Primary: Polymarket WebSocket RTDS (Chainlink feed — the exact price
 * Polymarket uses to resolve markets).
 * Fallback: polling /api/price every 5 seconds (CryptoCompare → Coinbase).
 * A "FALLBACK" banner must be shown to the user when RTDS is unavailable.
 */
export function usePrice(): PriceState {
  const [state, setState] = useState<PriceState>({
    price: null,
    source: 'FALLBACK',
    isFallback: true,
    lastUpdated: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rtdsActiveRef = useRef(false);

  const startPolling = () => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      if (rtdsActiveRef.current) return;
      try {
        const res = await fetch(`${BACKEND}/api/price`);
        if (!res.ok) return;
        const data = await res.json();
        if (typeof data.price === 'number') {
          setState({
            price: data.price,
            source: 'FALLBACK',
            isFallback: true,
            lastUpdated: Date.now(),
          });
        }
      } catch {
        // Network unavailable — keep last known price displayed
      }
    }, POLL_INTERVAL_MS);
  };

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const connectRTDS = () => {
    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        // Subscribe to the Chainlink price topic, filtered to btc/usd. The exact
        // payload shape is undocumented, so we send the common variants and keep
        // the polling fallback as a safety net (see onerror/onclose).
        ws.send(
          JSON.stringify({
            type: 'subscribe',
            topic: RTDS_TOPIC,
            filters: { symbol: RTDS_ASSET },
          })
        );
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data as string);
          // Some servers wrap the payload under `payload`/`data`.
          const body = msg?.payload ?? msg?.data ?? msg;
          // Filter for btc/usd price updates
          const asset = String(
            body?.asset ?? body?.symbol ?? body?.pair ?? msg?.symbol ?? ''
          ).toLowerCase();
          if (asset.includes('btc') || asset.includes('bitcoin')) {
            const price = parseFloat(
              body?.price ?? body?.value ?? body?.p ?? body?.data?.price
            );
            if (!isNaN(price) && price > 0) {
              rtdsActiveRef.current = true;
              stopPolling();
              setState({
                price,
                source: 'CHAINLINK_RTDS',
                isFallback: false,
                lastUpdated: Date.now(),
              });
            }
          }
        } catch {
          // Ignore parse errors
        }
      };

      ws.onerror = () => {
        rtdsActiveRef.current = false;
        startPolling();
      };

      ws.onclose = () => {
        rtdsActiveRef.current = false;
        wsRef.current = null;
        startPolling();
        // Attempt reconnect after 10 seconds
        setTimeout(connectRTDS, 10_000);
      };
    } catch {
      rtdsActiveRef.current = false;
      startPolling();
    }
  };

  useEffect(() => {
    // Always start polling immediately as safety net
    startPolling();
    // Then try RTDS — if it works it takes over and polling stops
    connectRTDS();

    return () => {
      stopPolling();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return state;
}
