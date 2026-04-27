export default function MarketOverview({ title, coins, variant }) {
  const bgClass = variant === 'gainers' ? 'border-green-500' : 'border-red-500';
  return (
    <div className={`bg-gray-800 rounded-lg p-4 border-l-4 ${bgClass}`}>
      <h3 className="text-lg font-semibold text-white mb-3">{title}</h3>
      <div className="space-y-2">
        {coins.map((coin) => (
          <div key={coin.id} className="flex justify-between items-center text-sm">
            <span className="text-gray-300">{coin.symbol.toUpperCase()}</span>
            <span className={`font-medium ${coin.price_change_percentage_24h >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              {coin.price_change_percentage_24h?.toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
