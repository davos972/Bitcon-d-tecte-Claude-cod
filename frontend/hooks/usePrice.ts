import { useCallback, useEffect, useRef, useState } from 'react';

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

export interface PriceApi extends PriceState {
  /**
   * Exact Chainlink value at a given UTC second, from the timestamped RTDS
   * buffer (initial snapshot backlog + live updates). This is the value
   * Polymarket resolves "Price to Beat" on. Returns null if that second is not
   * (yet) buffered — caller should fall back to the candle open.
   */
  getPriceAt: (tsSeconds: number) => number | null;
}

// Keep ~25 min of per-second Chainlink ticks (enough for a 15M window + margin).
const BUFFER_MAX = 1500;
// When the exact second is missing, accept the nearest earlier tick within this
// many seconds (Chainlink "last known value" semantics; gaps are rare).
const NEAREST_LOOKBACK = 120;

/**
 * Provides live BTC/USD price.
 *
 * Primary: Polymarket WebSocket RTDS (Chainlink feed — the exact price
 * Polymarket uses to resolve markets).
 * Fallback: polling /api/price every 5 seconds (CryptoCompare → Coinbase).
 * A "FALLBACK" banner must be shown to the user when RTDS is unavailable.
 */
export function usePrice(): PriceApi {
  const [state, setState] = useState<PriceState>({
    price: null,
    source: 'FALLBACK',
    isFallback: true,
    lastUpdated: null,
  });

  // Timestamped Chainlink ticks: UTC second → price. Fed from the RTDS snapshot
  // backlog and every live update, so we can read the exact window-open value.
  const bufferRef = useRef<Map<number, number>>(new Map());

  const addTick = (tsMs: number, value: number) => {
    if (!Number.isFinite(tsMs) || !Number.isFinite(value) || value <= 0) return;
    const sec = Math.floor(tsMs / 1000);
    const buf = bufferRef.current;
    buf.set(sec, value);
    if (buf.size > BUFFER_MAX) {
      // Drop the oldest second (Map preserves insertion order).
      const oldest = buf.keys().next().value;
      if (oldest !== undefined) buf.delete(oldest);
    }
  };

  const getPriceAt = useCallback((tsSeconds: number): number | null => {
    const buf = bufferRef.current;
    const exact = buf.get(tsSeconds);
    if (exact !== undefined) return exact;
    for (let t = tsSeconds - 1; t >= tsSeconds - NEAREST_LOOKBACK; t--) {
      const v = buf.get(t);
      if (v !== undefined) return v;
    }
    return null;
  }, []);

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

          // Buffer timestamped ticks so getPriceAt() can return the exact
          // window-open value (matches Polymarket's Chainlink resolution).
          if (snapshot) {
            for (const t of snapshot) {
              addTick(Number(t?.timestamp), parseFloat(t?.value));
            }
          } else if (payload?.timestamp != null) {
            addTick(Number(payload.timestamp), parseFloat(payload.value));
          }

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

  return { ...state, getPriceAt };
}
