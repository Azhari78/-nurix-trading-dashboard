# Crypto Dashboard + Auto Trading Bot

Dashboard ini sekarang ada:
- Live market data Binance (REST + WebSocket)
- Auto bot strategy (MA crossover + stop loss + take profit)
- Execution mode:
  - `Paper` (simulasi)
  - `Live` (hantar order Binance melalui backend signed API)

## 1) Setup

```bash
npm install
cp .env.example .env
```

Isi `.env`:
- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`
- `BINANCE_BASE_URL` (default production Binance)
- `ENABLE_LIVE_ORDERS`
  - `false` = hanya test order
  - `true` = benarkan order sebenar

## 2) Jalankan App

Terminal 1 (backend trading API):
```bash
npm run dev:server
```

Terminal 2 (frontend):
```bash
npm run dev
```

Frontend default: `http://localhost:5173`  
Backend default: `http://localhost:8787`

## 2b) Run 24 Jam (Tanpa Terminal Lokal)

Kalau nak sistem sentiasa hidup 24/7, deploy ke server cloud (contoh: Render).

Backend (`Web Service`):
- Build Command: `npm install`
- Start Command: `npm run dev:server`
- Env vars wajib:
  - `BINANCE_API_KEY`
  - `BINANCE_API_SECRET`
  - `BINANCE_BASE_URL`
  - `ENABLE_LIVE_ORDERS` (mula dengan `false`)
  - `TRADING_ALLOWED_ORIGINS` (letak URL frontend, contoh `https://your-frontend.onrender.com`)

Frontend (`Static Site`):
- Build Command: `npm install && npm run build`
- Publish Directory: `dist`
- Env var:
  - `VITE_TRADING_API_URL=https://your-backend.onrender.com`

Nota:
- Plan free biasanya ada auto-sleep bila tak ada traffic.
- Untuk betul-betul 24/7, guna plan yang tak tidur (always-on/no-sleep).

## 3) Live Trading Flow

1. Buka panel `Auto Trading Bot`.
2. Tukar mode ke `Live`.
3. Semak status API (Configured/Connected).
4. Guna `TEST order` dulu.
5. Bila dah yakin, set `ENABLE_LIVE_ORDERS=true` dan matikan `TEST order`.

## 4) Endpoint Backend

- `GET /api/binance/status`
- `GET /api/binance/account`
- `POST /api/binance/order`

Semua signing request Binance dibuat di backend supaya API secret tidak bocor ke frontend.

## Notes

- Binance rule (min notional / lot size) ikut pasangan coin. Kalau quantity tak valid, Binance akan reject order.
- Guna API key dengan permission minimum yang perlu sahaja.
