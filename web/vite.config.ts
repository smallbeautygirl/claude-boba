import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // 綁 0.0.0.0 —— 預設只聽 127.0.0.1，從別台機器（例如你的筆電連開發機）開不起來。
  server: { host: true },
  plugins: [react()],
})
