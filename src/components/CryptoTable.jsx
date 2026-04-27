export default function CryptoTable({ coins, onSelectCoin }) {
  return (
    <div className="bg-gray-800 rounded-lg overflow-hidden shadow-lg">
      <div className="p-4 border-b border-gray-700">
        <h2 className="text-xl font-bold text-white">Market Watchlist (Binance)</h2>
      </div>
      <div className="overflow-x-auto max-h-96 overflow-y-auto">
        <table className="w-full text-white">
          <thead className="bg-gray-700 sticky top-0">
            <tr>
              <th className="px-4 py-2 text-left">Coin</th>
              <th className="px-4 py-2 text-right">Price (USDT)</th>
              <th className="px-4 py-2 text-right">24h %</th>
            </tr>
          </thead>
          <tbody>
            {coins.map((coin) => (
              <tr
                key={coin.symbol}
                onClick={() => onSelectCoin(coin.symbol)}
                className="border-b border-gray-700 hover:bg-gray-700 cursor-pointer"
              >
                <td className="px-4 py-2">
                  <div className="font-medium">{coin.name}</div>
                  <div className="text-sm text-gray-400">{coin.symbol}</div>
                </td>
                <td className="px-4 py-2 text-right">
                  ${coin.current_price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
                </td>
                <td className={`px-4 py-2 text-right font-semibold ${coin.price_change_percentage_24h >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                  {coin.price_change_percentage_24h?.toFixed(2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
