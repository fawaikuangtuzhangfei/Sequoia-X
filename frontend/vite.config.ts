import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // 开发态浏览器只访问 vite 的源，/api 由这里转发到 FastAPI。
    // 因此后端不需要 CORS —— 见 sequoia_x/api/app.py 的说明。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000' },
    },
  },
  build: {
    // 产物由 FastAPI 挂在 "/" 下提供，保持默认的绝对路径 base
    outDir: 'dist',
  },
})
