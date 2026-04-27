import { createHmac } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const parseBoolean = (value, defaultValue = false) => {
  if (value === undefined) return defaultValue;
  const normalized = String(value).trim().toLowerCase();
  return normalized === '1' || normalized === 'true' || normalized === 'yes';
};

const normalizeDecimal = (value, maxDigits = 8) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  return numeric.toFixed(maxDigits).replace(/\.?0+$/, '');
};

const getAssetFreeBalance = (balances, asset) => {
  if (!Array.isArray(balances)) return 0;
  const row = balances.find((item) => item?.asset === asset);
  const free = Number.parseFloat(row?.free ?? '0');
  return Number.isFinite(free) ? free : 0;
};

const loadEnvFile = (envPath) => {
  if (!existsSync(envPath)) return;
  const raw = readFileSync(envPath, 'utf8');
  const lines = raw.split(/\r?\n/);

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const separatorIndex = trimmed.indexOf('=');
    if (separatorIndex <= 0) return;

    const key = trimmed.slice(0, separatorIndex).trim();
    let value = trimmed.slice(separatorIndex + 1).trim();

    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }

    if (process.env[key] === undefined) {
      process.env[key] = value;
    }
  });
};

loadEnvFile(path.resolve(__dirname, '../.env'));

const PORT = Number.parseInt(process.env.PORT ?? process.env.TRADING_BOT_PORT ?? '8787', 10);
const BINANCE_BASE_URL = process.env.BINANCE_BASE_URL ?? 'https://api.binance.com';
const BINANCE_API_KEY = process.env.BINANCE_API_KEY ?? '';
const BINANCE_API_SECRET = process.env.BINANCE_API_SECRET ?? '';
const RECV_WINDOW = Number.parseInt(process.env.BINANCE_RECV_WINDOW ?? '5000', 10);
const ENABLE_LIVE_ORDERS = parseBoolean(process.env.ENABLE_LIVE_ORDERS, false);
const ALLOWED_ORIGINS = (process.env.TRADING_ALLOWED_ORIGINS ?? 'http://localhost:5173,http://127.0.0.1:5173')
  .split(',')
  .map((origin) => origin.trim())
  .filter(Boolean);

const isConfigured = () => Boolean(BINANCE_API_KEY && BINANCE_API_SECRET);

const buildCorsHeaders = (originHeader) => {
  const allowAll = ALLOWED_ORIGINS.includes('*');
  const allowOrigin = allowAll
    ? '*'
    : (originHeader && ALLOWED_ORIGINS.includes(originHeader) ? originHeader : ALLOWED_ORIGINS[0] || '*');

  return {
    'Access-Control-Allow-Origin': allowOrigin,
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    Vary: 'Origin',
  };
};

const jsonResponse = (res, statusCode, payload, corsHeaders = {}) => {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json',
    ...corsHeaders,
  });
  res.end(JSON.stringify(payload));
};

const readJsonBody = async (req) => {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString('utf8').trim();
  if (!raw) return {};
  return JSON.parse(raw);
};

const createSignature = (queryString) =>
  createHmac('sha256', BINANCE_API_SECRET).update(queryString).digest('hex');

const binanceRequest = async ({
  pathname,
  method = 'GET',
  params = {},
  signed = false,
}) => {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    query.append(key, String(value));
  });

  if (signed) {
    query.append('timestamp', String(Date.now()));
    query.append('recvWindow', String(RECV_WINDOW));
    query.append('signature', createSignature(query.toString()));
  }

  const url = `${BINANCE_BASE_URL}${pathname}${query.size > 0 ? `?${query.toString()}` : ''}`;
  const response = await fetch(url, {
    method,
    headers: signed
      ? {
          'X-MBX-APIKEY': BINANCE_API_KEY,
        }
      : undefined,
  });

  const raw = await response.text();
  let payload = null;
  try {
    payload = raw ? JSON.parse(raw) : {};
  } catch {
    payload = { raw };
  }

  if (!response.ok) {
    const message = payload?.msg || `Binance error (${response.status})`;
    const error = new Error(message);
    error.statusCode = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
};

const requireApiConfig = () => {
  if (!isConfigured()) {
    const error = new Error('BINANCE_API_KEY / BINANCE_API_SECRET belum diset pada server.');
    error.statusCode = 400;
    throw error;
  }
};

const handleStatus = async (res, corsHeaders) => {
  let serverTime = null;
  let apiConnected = false;

  try {
    const payload = await binanceRequest({ pathname: '/api/v3/time' });
    serverTime = payload?.serverTime ?? null;
    apiConnected = true;
  } catch {
    apiConnected = false;
  }

  jsonResponse(
    res,
    200,
    {
      ok: true,
      configured: isConfigured(),
      apiConnected,
      baseUrl: BINANCE_BASE_URL,
      serverTime,
      liveOrdersEnabled: ENABLE_LIVE_ORDERS,
    },
    corsHeaders
  );
};

const handleAccount = async (res, corsHeaders) => {
  requireApiConfig();
  const payload = await binanceRequest({
    pathname: '/api/v3/account',
    method: 'GET',
    signed: true,
  });

  const balances = Array.isArray(payload?.balances) ? payload.balances : [];
  const usdtFree = getAssetFreeBalance(balances, 'USDT');

  jsonResponse(
    res,
    200,
    {
      ok: true,
      accountType: payload?.accountType ?? null,
      canTrade: payload?.canTrade ?? false,
      updateTime: payload?.updateTime ?? null,
      makerCommission: payload?.makerCommission ?? null,
      takerCommission: payload?.takerCommission ?? null,
      usdtFree,
      balances,
    },
    corsHeaders
  );
};

const handleOrder = async (req, res, corsHeaders) => {
  requireApiConfig();
  const body = await readJsonBody(req);

  const symbol = String(body?.symbol ?? '').toUpperCase();
  const side = String(body?.side ?? '').toUpperCase();
  const type = String(body?.type ?? 'MARKET').toUpperCase();
  const testMode = parseBoolean(body?.testMode, false);

  if (!symbol || !/^[A-Z0-9]{6,20}$/.test(symbol)) {
    jsonResponse(res, 400, { ok: false, error: 'Symbol tidak valid.' }, corsHeaders);
    return;
  }

  if (!['BUY', 'SELL'].includes(side)) {
    jsonResponse(res, 400, { ok: false, error: 'Side mesti BUY atau SELL.' }, corsHeaders);
    return;
  }

  if (type !== 'MARKET') {
    jsonResponse(res, 400, { ok: false, error: 'Hanya MARKET order disokong buat masa ni.' }, corsHeaders);
    return;
  }

  if (!testMode && !ENABLE_LIVE_ORDERS) {
    jsonResponse(
      res,
      403,
      {
        ok: false,
        error: 'LIVE order dimatikan. Set ENABLE_LIVE_ORDERS=true untuk benarkan order sebenar.',
      },
      corsHeaders
    );
    return;
  }

  const params = {
    symbol,
    side,
    type,
    newOrderRespType: 'RESULT',
  };

  const quantity = normalizeDecimal(body?.quantity);
  const quoteOrderQty = normalizeDecimal(body?.quoteOrderQty, 2);

  if (side === 'BUY') {
    if (!quantity && !quoteOrderQty) {
      jsonResponse(res, 400, { ok: false, error: 'BUY perlu quantity atau quoteOrderQty.' }, corsHeaders);
      return;
    }
    if (quantity) params.quantity = quantity;
    if (quoteOrderQty) params.quoteOrderQty = quoteOrderQty;
  }

  if (side === 'SELL') {
    if (!quantity) {
      jsonResponse(res, 400, { ok: false, error: 'SELL perlu quantity.' }, corsHeaders);
      return;
    }
    params.quantity = quantity;
  }

  const pathname = testMode ? '/api/v3/order/test' : '/api/v3/order';
  const payload = await binanceRequest({
    pathname,
    method: 'POST',
    params,
    signed: true,
  });

  jsonResponse(
    res,
    200,
    {
      ok: true,
      testMode,
      symbol,
      side,
      order: testMode ? null : payload,
      accepted: testMode ? true : undefined,
    },
    corsHeaders
  );
};

const server = createServer(async (req, res) => {
  const corsHeaders = buildCorsHeaders(req.headers.origin);

  if (req.method === 'OPTIONS') {
    res.writeHead(204, corsHeaders);
    res.end();
    return;
  }

  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);

  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      jsonResponse(res, 200, { ok: true }, corsHeaders);
      return;
    }

    if (req.method === 'GET' && url.pathname === '/api/binance/status') {
      await handleStatus(res, corsHeaders);
      return;
    }

    if (req.method === 'GET' && url.pathname === '/api/binance/account') {
      await handleAccount(res, corsHeaders);
      return;
    }

    if (req.method === 'POST' && url.pathname === '/api/binance/order') {
      await handleOrder(req, res, corsHeaders);
      return;
    }

    jsonResponse(res, 404, { ok: false, error: 'Route tidak ditemui.' }, corsHeaders);
  } catch (error) {
    jsonResponse(
      res,
      error?.statusCode || 500,
      {
        ok: false,
        error: error?.message || 'Internal server error.',
        details: error?.payload || null,
      },
      corsHeaders
    );
  }
});

server.listen(PORT, () => {
  console.log(`[trading-api] running at http://localhost:${PORT}`);
  console.log(`[trading-api] base url: ${BINANCE_BASE_URL}`);
  console.log(`[trading-api] live orders enabled: ${ENABLE_LIVE_ORDERS ? 'YES' : 'NO (test only)'}`);
});
