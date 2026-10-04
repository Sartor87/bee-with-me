import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  envDir: '../',
  server: {
    proxy: {
      '/api':     'http://localhost:8000',
      '/ws':      { target: 'ws://localhost:8000', ws: true },
      '/tiles':   'http://localhost:8000',
      '/uploads': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.js'],
    // The alarm ring gap uses the monotonic clock (B45): fake it together with Date.
    fakeTimers: {
      toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'clearImmediate', 'Date', 'performance'],
    },
  },
})
