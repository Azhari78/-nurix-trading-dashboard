import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import useWebSocket from './hooks/useWebSocket';
import Header from './components/Header';
import CryptoTable from './components/CryptoTable';
import MarketOverview from './components/MarketOverview';
import PriceChart from './components/PriceChart';
import YearHighLow from './components/YearHighLow';
import AutoTradingBot from './components/AutoTradingBot';

const BINANCE_REST_ENDPOINTS = [
  'https://api.binance.com/api/v3/ticker/24hr',
  'https://data-api.binance.vision/api/v3/ticker/24hr',
];

function App() {
  const [coins, setCoins] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCoin, setSelectedCoin] = useState('BTC');
  const [searchTerm, setSearchTerm] = useState('');
  const [indicatorSnapshot, setIndicatorSnapshot] = useState(null);

  // Apply websocket updates in a single state update to prevent UI thrashing.
  const handlePriceUpdate = useCallback((updatesBySymbol) => {
    setCoins((prevCoins) => {
      if (prevCoins.length === 0) {
        return prevCoins;
      }

      let hasChanges = false;
      const nextCoins = prevCoins.map((coin) => {
        const next = updatesBySymbol[coin.symbol];
        if (!next) {
          return coin;
        }
        if (
          coin.current_price === next.current_price &&
          coin.price_change_percentage_24h === next.price_change_percentage_24h
        ) {
          return coin;
        }

        hasChanges = true;
        return {
          ...coin,
          current_price: next.current_price,
          price_change_percentage_24h: next.price_change_percentage_24h,
        };
      });

      return hasChanges ? nextCoins : prevCoins;
    });
  }, []);

  useWebSocket(handlePriceUpdate, !loading && coins.length > 0);

  // Fetch initial 24hr ticker data from Binance REST API
  useEffect(() => {
    const fetchBinanceData = async () => {
      try {
        let data = null;
        let lastError = null;

        for (const endpoint of BINANCE_REST_ENDPOINTS) {
          try {
            const response = await axios.get(endpoint, { timeout: 5000 });
            if (!Array.isArray(response.data)) {
              throw new Error('Unexpected Binance response format');
            }
            data = response.data;
            break;
          } catch (endpointError) {
            lastError = endpointError;
          }
        }

        if (!data) {
          throw lastError || new Error('Unable to fetch Binance data');
        }

        // Filter only USDT pairs and map to our coin structure
        const usdtPairs = data.filter(item => item.symbol.endsWith('USDT'));
        // Limit to top 50 by quote volume (or just take first 50)
        const sorted = usdtPairs.sort((a, b) => parseFloat(b.quoteVolume) - parseFloat(a.quoteVolume));
        const top50 = sorted.slice(0, 50);

        const mappedCoins = top50.map(item => ({
          id: item.symbol.replace('USDT', '').toLowerCase(),
          symbol: item.symbol.replace('USDT', ''),
          name: item.symbol.replace('USDT', ''), // You could map to full names later
          current_price: parseFloat(item.lastPrice),
          price_change_percentage_24h: parseFloat(item.priceChangePercent),
          volume: parseFloat(item.volume),
          quoteVolume: parseFloat(item.quoteVolume),
          high_24h: parseFloat(item.highPrice),
          low_24h: parseFloat(item.lowPrice)
        }));

        setCoins(mappedCoins);
        setError(null);
      } catch (err) {
        console.error('Binance API error:', err);
        setError('Failed to fetch Binance data. Using fallback data.');
        // Fallback mock data (Binance-like)
        setCoins([
          { id: 'btc', symbol: 'BTC', name: 'Bitcoin', current_price: 65000, price_change_percentage_24h: 2.5 },
          { id: 'eth', symbol: 'ETH', name: 'Ethereum', current_price: 3500, price_change_percentage_24h: -1.2 },
          { id: 'bnb', symbol: 'BNB', name: 'BNB', current_price: 600, price_change_percentage_24h: 0.8 },
          { id: 'sol', symbol: 'SOL', name: 'Solana', current_price: 180, price_change_percentage_24h: 5.3 },
          { id: 'xrp', symbol: 'XRP', name: 'XRP', current_price: 0.62, price_change_percentage_24h: 1.5 },
        ]);
      } finally {
        setLoading(false);
      }
    };
    fetchBinanceData();
  }, []);

  const filteredCoins = coins.filter(coin =>
    coin.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    coin.symbol.toLowerCase().includes(searchTerm.toLowerCase())
  );
  const selectedCoinData = coins.find((coin) => coin.symbol === selectedCoin);

  const sortedByChange = [...coins].sort((a, b) => b.price_change_percentage_24h - a.price_change_percentage_24h);
  const topGainers = sortedByChange.slice(0, 5);
  const topLosers = sortedByChange.slice(-5).reverse();

  if (loading) {
    return <div className="min-h-screen bg-gray-900 flex items-center justify-center text-white text-xl">Loading Binance data...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <Header searchTerm={searchTerm} setSearchTerm={setSearchTerm} />
      {error && (
        <div className="bg-yellow-800 text-yellow-200 p-2 text-center text-sm">
          ⚠️ {error} — Using demo data.
        </div>
      )}
      <main className="container mx-auto px-4 py-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
          <MarketOverview title="Top Gainers (Binance)" coins={topGainers} variant="gainers" />
          <MarketOverview title="Top Losers (Binance)" coins={topLosers} variant="losers" />
          <YearHighLow coins={coins.slice(0, 10)} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <PriceChart symbol={selectedCoin} onIndicatorUpdate={setIndicatorSnapshot} />
          </div>
          <div className="lg:col-span-1">
            <CryptoTable coins={filteredCoins} onSelectCoin={setSelectedCoin} />
          </div>
        </div>
        <div className="mt-6">
          <AutoTradingBot
            symbol={selectedCoin}
            currentPrice={selectedCoinData?.current_price}
            marketChange24h={selectedCoinData?.price_change_percentage_24h}
            indicatorSnapshot={indicatorSnapshot}
          />
        </div>
      </main>
    </div>
  );
}

export default App;
