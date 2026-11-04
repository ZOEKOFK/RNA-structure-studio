import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // ECharts 按需引入后仍有约 576 kB，Mol* 查看器 chunk 约 5.3 MB，
    // 两者都只在对应页面懒加载，不影响首屏，故放宽警告阈值
    chunkSizeWarningLimit: 6000,
    rollupOptions: {
      output: {
        // 把较大的第三方依赖拆成独立 chunk，首屏更小且便于长期缓存
        manualChunks: {
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          vue: ['vue', 'vue-router'],
        },
      },
    },
  },
})