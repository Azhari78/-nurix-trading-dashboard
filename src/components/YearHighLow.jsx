import { useEffect, useState } from 'react';

export default function YearHighLow({ coins }) {
  const [highLow, setHighLow] = useState([]);

  useEffect(() => {
    if (coins.length === 0) return;
    const data = coins.slice(0, 5).map(coin => ({
      symbol: coin.symbol.toUpperCase(),
      high: (coin.current_price * 1.25).toFixed(2),
      low: (coin.current_price * 0.75).toFixed(2),
      current: coin.current_price
    }));
    setHighLow(data);
  }, [coins]);

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-lg font-semibold text-white mb-3">52‑Week Range (approx)</h3>
      <div className="space-y-3">
        {highLow.map((item, idx) => {
          const range = item.high - item.low;
          const position = ((item.current - item.low) / range) * 100;
          return (
            <div key={idx} className="text-sm">
              <div className="flex justify-between text-gray-300 mb-1">
                <span>{item.symbol}</span>
                <span>${item.low} – ${item.high}</span>
              </div>
              <div className="w-full bg-gray-700 rounded-full h-1.5 relative">
                <div className="bg-blue-500 h-1.5 rounded-full" style={{ width: `${position}%` }}></div>
                <div className="absolute top-1/2 transform -translate-y-1/2 w-2 h-2 bg-white rounded-full" style={{ left: `calc(${position}% - 4px)` }}></div>
              </div>
              <div className="text-right text-xs text-gray-400 mt-1">Current: ${item.current.toLocaleString()}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
