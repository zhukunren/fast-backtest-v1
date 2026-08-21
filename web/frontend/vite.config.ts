import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export function isSameOriginDevRequest(origin: string | undefined, devServerAddress: string | undefined): boolean {
  if (!origin || !devServerAddress) {
    return false
  }
  try {
    const parsed = new URL(origin)
    return parsed.protocol === 'http:'
      && parsed.pathname === '/'
      && parsed.search === ''
      && parsed.hash === ''
      && parsed.host.toLowerCase() === devServerAddress.toLowerCase()
  } catch {
    return false
  }
}

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/__frontend/' : '/',
  plugins: [vue()],
  test: {
    environment: 'happy-dom',
    fileParallelism: false,
    setupFiles: ['./src/test/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
    testTimeout: 15000,
    hookTimeout: 15000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      reportsDirectory: '../../output/tools/frontend_coverage',
      include: [
        'src/api/**/*.ts',
        'src/utils/chart.ts',
        'src/utils/returnHeatmap.ts',
        'src/features/detail/holdingsAggregation.ts',
        'src/components/EmptyState.vue',
      ],
      thresholds: {
        perFile: true,
        statements: 90,
        branches: 80,
      },
    },
  },
  build: {
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.replaceAll('\\', '/')

          if (normalized.includes('/node_modules/zrender/')) {
            return 'vendor-zrender'
          }
          if (normalized.includes('/node_modules/echarts/lib/chart/')) {
            return 'vendor-echarts-charts'
          }
          if (normalized.includes('/node_modules/echarts/lib/component/')) {
            return 'vendor-echarts-components'
          }
          if (normalized.includes('/node_modules/echarts/lib/coord/')) {
            return 'vendor-echarts-coordinates'
          }
          if (
            normalized.includes('/node_modules/echarts/')
            || normalized.includes('/src/lib/echarts.ts')
          ) {
            return 'vendor-echarts-core'
          }
          if (
            normalized.includes('node_modules/vue')
            || normalized.includes('node_modules/@vue')
          ) {
            return 'vendor-vue'
          }
          return undefined
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
    },
  },
}))
