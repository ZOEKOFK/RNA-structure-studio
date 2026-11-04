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
    <span class="brand">🧬 RNA 结构预测分析平台</span>
    <router-link to="/">首页</router-link>
    <router-link to="/fold">折叠视图</router-link>
    <router-link to="/mutate">突变对比</router-link>
    <router-link to="/deep">深度分析</router-link>
    <router-link to="/tertiary">3D 结构</router-link>
    <span style="margin-left: auto;" class="engine-pill" :class="{ off: !ok }">
      <span class="dot"></span>
      {{ engine }}<span v-if="version"> {{ version }}</span>
      <span class="sep">|</span>
      {{ deep }}
    </span>
  </header>

  <div v-if="toast" class="toast" @click="toast = ''">
    {{ toast }}
    <span class="toast-close">×</span>
  </div>

  <router-view />
</template>

<style scoped>
.engine-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  color: var(--muted);
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 12px;
}
.engine-pill .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
}
.engine-pill.off .dot { background: var(--red); }
.engine-pill .sep { opacity: 0.45; }

.toast {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 100;
  max-width: 360px;
  background: #3a1f24;
  border: 1px solid var(--red);
  color: var(--text);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 0.88rem;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
  cursor: pointer;
}
.toast-close { margin-left: 10px; color: var(--muted); }
</style>
