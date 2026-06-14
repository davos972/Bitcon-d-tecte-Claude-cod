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
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rtdsActiveRef = useRef(false);

  const stopPing = () => {
    if (pingRef.current) {
      clearInterval(pingRef.current);
      pingRef.current = null;
    }
  };

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
        // Official RTDS subscribe format (verified live against the server):
        // action + subscriptions[] with the symbol filter as a JSON *string*.
        ws.send(
          JSON.stringify({
            action: 'subscribe',
            subscriptions: [
              { topic: RTDS_TOPIC, type: '*', filters: `{"symbol":"${RTDS_ASSET}"}` },
            ],
          })
        );
        // The server drops idle connections — keepalive with a raw PING every 5s.
        stopPing();
        pingRef.current = setInterval(() => {
          try {
            ws.send('PING');
          } catch {
            // socket closing — onclose will handle reconnect
          }
        }, 5000);
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data as string);
          // Live update shape:   { topic, type:"update", payload:{ symbol, value } }
          // Initial snapshot:     { payload:{ data:[ { timestamp, value }, ... ] } }
          const payload = msg?.payload ?? msg;
          const symbol = String(payload?.symbol ?? '').toLowerCase();
          // For the snapshot array (no symbol field), take the latest value — we
          // only ever subscribe to btc/usd, so it is unambiguous.
          const snapshot = Array.isArray(payload?.data) ? payload.data : null;
          const rawValue = snapshot
            ? snapshot[snapshot.length - 1]?.value
            : payload?.value;

          const isBtc = snapshot != null || symbol.includes('btc');
          if (isBtc) {
            const price = parseFloat(rawValue);
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
        stopPing();
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
      stopPing();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return state;
}
