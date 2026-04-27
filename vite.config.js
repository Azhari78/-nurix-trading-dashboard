import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const tradingApiProxyTarget = process.env.VITE_TRADING_API_PROXY_TARGET || 'http://localhost:8787'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api/binance': {
        target: tradingApiProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
