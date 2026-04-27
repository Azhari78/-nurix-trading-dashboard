import { useEffect, useRef } from 'react';

export default function useWebSocket(onPriceUpdate, enabled = true) {
  const ws = useRef(null);

  useEffect(() => {
    if (!enabled || typeof onPriceUpdate !== 'function') {
      return undefined;
    }

    // Use combined ticker stream, then batch updates into one callback.
    ws.current = new WebSocket('wss://stream.binance.com:9443/ws/!ticker@arr');

    ws.current.onopen = () => console.log('✅ Binance WebSocket connected – live prices active');

    ws.current.onmessage = (event) => {
      let data = null;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (!Array.isArray(data)) {
        return;
      }

      const updatesBySymbol = {};
      let updateCount = 0;
      data.forEach((ticker) => {
        if (!ticker?.s?.endsWith('USDT')) {
          return;
        }
        const symbol = ticker.s.slice(0, -4);
        const price = Number.parseFloat(ticker.c);
        const change = Number.parseFloat(ticker.P);
        if (Number.isNaN(price) || Number.isNaN(change)) {
          return;
        }
        updatesBySymbol[symbol] = {
          current_price: price,
          price_change_percentage_24h: change,
        };
        updateCount += 1;
      });

      if (updateCount > 0) {
        onPriceUpdate(updatesBySymbol);
      }
    };

    ws.current.onerror = (err) => {
      console.warn('Binance WebSocket error:', err);
    };

    return () => {
      if (ws.current) ws.current.close();
    };
  }, [enabled, onPriceUpdate]);
}
