const API_BASE = (import.meta.env.VITE_TRADING_API_URL || '').replace(/\/$/, '');

const toUrl = (path) => (API_BASE ? `${API_BASE}${path}` : path);

const parseJson = async (response) => {
  try {
    return await response.json();
  } catch {
    return null;
  }
};

const request = async (path, options = {}) => {
  const response = await fetch(toUrl(path), {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    body: options.body,
  });

  const payload = await parseJson(response);
  if (!response.ok || payload?.ok === false) {
    const error = new Error(payload?.error || `Request failed (${response.status})`);
    error.payload = payload;
    throw error;
  }

  return payload;
};

export const fetchTradingStatus = () => request('/api/binance/status');

export const fetchBinanceAccount = () => request('/api/binance/account');

export const placeBinanceOrder = (orderPayload) =>
  request('/api/binance/order', {
    method: 'POST',
    body: JSON.stringify(orderPayload),
  });
