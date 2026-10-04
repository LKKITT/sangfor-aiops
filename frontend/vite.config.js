import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8600', changeOrigin: true, ws: true }
    }
  },
  build: {
    rollupOptions: {
      output: {
        // vendor 分片：UI 框架/图标/核心库独立成块，业务代码改动不再使框架缓存失效
        manualChunks: {
          'element-plus': ['element-plus'],
          'element-icons': ['@element-plus/icons-vue'],
          vendor: ['vue']
        }
      }
    }
  }
})
