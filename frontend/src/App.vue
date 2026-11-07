<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from './api'

const engine = ref('')
const version = ref('')
const deep = ref('')
const ok = ref(false)
const toast = ref('')

function onApiError(event) {
  toast.value = event.detail
  scheduleDismiss()
}

let toastTimer = null
function scheduleDismiss() {
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 6000)
}

onMounted(async () => {
  window.addEventListener('rna:error', onApiError)
  try {
    const h = await api.health()
    ok.value = true
    engine.value = h.rnafold_available ? 'ViennaRNA' : '降级引擎'
    version.value = h.viennarna_version || ''
    deep.value = h.deep_engine === 'rna-fm' ? '深度 RNA-FM' : '深度降级'
  } catch {
    engine.value = '后端未连接'
    ok.value = false
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('rna:error', onApiError)
  clearTimeout(toastTimer)
})
</script>

<template>
  <header class="nav">
    <router-link to="/" class="brand">
      <span class="brand-mark">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
          <path d="M7 3c0 4.5 10 4.5 10 9s-10 4.5-10 9" />
          <path d="M17 3c0 4.5-10 4.5-10 9s10 4.5 10 9" />
          <path d="M8.4 7.4h7.2M8.4 16.6h7.2M9.6 12h4.8" />
        </svg>
      </span>
      <span class="brand-text">RNA Fold Lab</span>
      <span class="brand-sub">结构预测分析平台</span>
    </router-link>

    <nav class="nav-links">
      <router-link to="/" class="nav-link">首页</router-link>
      <router-link to="/fold" class="nav-link">折叠视图</router-link>
      <router-link to="/mutate" class="nav-link">突变对比</router-link>
      <router-link to="/deep" class="nav-link">深度分析</router-link>
      <router-link to="/tertiary" class="nav-link">3D 结构</router-link>
    </nav>

    <span class="engine-pill" :class="{ off: !ok }" style="margin-left: auto;">
      <span class="dot"></span>
      <span class="engine-name">{{ engine }}</span>
      <span v-if="version">{{ version }}</span>
      <span class="sep">/</span>
      <span>{{ deep }}</span>
    </span>
  </header>

  <router-view v-slot="{ Component, route }">
    <transition name="route" mode="out-in">
      <component :is="Component" :key="route.path" />
    </transition>
  </router-view>

  <footer class="footer">
    <div>RNA Fold Lab · 本地推理，序列不出内网</div>
    <div class="footer-links">
      <span>ViennaRNA</span>
      <span>RNA-FM</span>
      <span>forna</span>
      <span>Mol*</span>
      <span>ECharts</span>
    </div>
  </footer>

  <div v-if="toast" class="toast" @click="toast = ''">
    <span class="toast-icon">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
    </span>
    <span>{{ toast }}</span>
    <span class="toast-close">×</span>
  </div>
</template>
