<script setup>
// 嵌入式 Mol* 查看器：直接挂载官方 Viewer（不依赖外网 iframe），
// 坐标优先走本地 /api/pdb/{id}，未收录时由父组件传入 RCSB 下载地址。
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  url: { type: String, default: '' },
  height: { type: Number, default: 560 },
})

const host = ref(null)
const status = ref('idle')
const message = ref('')
let viewer = null
let pending = 0

async function ensureViewer() {
  if (viewer) return viewer
  const [{ Viewer }] = await Promise.all([
    import('molstar/lib/apps/viewer/app'),
    import('molstar/lib/mol-plugin-ui/skin/light.scss'),
  ])
  viewer = await Viewer.create(host.value, {
    layoutIsExpanded: false,
    layoutShowControls: false,
    layoutShowRemoteState: false,
    layoutShowSequence: false,
    layoutShowLog: false,
    viewportShowExpand: false,
    viewportShowSelectionMode: false,
    viewportShowAnimation: false,
    viewportShowSettings: false,
  })
  return viewer
}

async function waitForCanvas(timeout = 2500) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeout) {
    if (host.value && host.value.querySelector('canvas')) return true
    await new Promise((r) => setTimeout(r, 120))
  }
  return false
}

async function load() {
  if (!props.url || !host.value) return
  const token = ++pending
  status.value = 'loading'
  message.value = ''
  try {
    const v = await ensureViewer()
    // 无 GPU / 关了硬件加速时 Mol* 只在面板里打一行英文提示，画布根本不会创建；
    // 这里主动探测并给出中文降级说明，避免用户以为页面坏了。
    if (!(await waitForCanvas()) && token === pending) {
      status.value = 'unsupported'
      message.value = '当前浏览器无法创建 WebGL 渲染上下文'
      return
    }
    await v.loadStructureFromUrl(props.url, 'pdb')
    if (token === pending) status.value = 'ready'
  } catch (e) {
    if (token === pending) {
      status.value = 'error'
      message.value = e?.message || String(e)
    }
  }
}

watch(() => props.url, load)
onMounted(load)
onBeforeUnmount(() => {
  if (viewer && viewer.plugin) viewer.plugin.dispose()
  viewer = null
})
</script>

<template>
  <div class="molstar-wrap">
    <div ref="host" :style="{ width: '100%', height: height + 'px' }"></div>
    <div v-if="status === 'loading'" class="viewer-status">
      <span class="spinner"></span> 3D 结构加载中…
    </div>
    <div v-if="status === 'unsupported'" class="viewer-fallback">
      <strong>这个浏览器没能创建 WebGL 画布</strong>
      <p>
        常见原因是关闭了「使用硬件加速」、在远程桌面 / 虚拟机里，或显卡驱动过旧。
        可先打开浏览器的硬件加速开关再刷新；也可以把坐标下载下来，用 PyMOL、ChimeraX
        或 Mol* 官方网页查看。
      </p>
      <a :href="url" target="_blank" rel="noreferrer" class="link-btn secondary">下载 PDB 坐标</a>
    </div>
    <p v-if="status === 'error'" class="error" style="margin: 10px;">
      Mol* 加载失败：{{ message }}
      <a :href="url" target="_blank" rel="noreferrer">下载原始 PDB</a>
    </p>
  </div>
</template>

<style scoped>
.molstar-wrap {
  position: relative;
  margin-top: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius, 14px);
  overflow: hidden;
  background: #f8fafc;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.6);
}
.molstar-wrap :deep(.msp-plugin) {
  height: 100%;
}
.viewer-fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  text-align: center;
  background: var(--surface-2, #f8fafc);
  color: var(--muted);
  font-size: 0.86rem;
}
.viewer-fallback strong {
  color: var(--text, #0d1b2f);
  font-size: 0.96rem;
}
.viewer-fallback p { max-width: 44ch; line-height: 1.65; margin: 0; }

.viewer-status {
  position: absolute;
  top: 12px;
  left: 14px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  font-size: 0.8rem;
  color: var(--muted);
  backdrop-filter: blur(8px);
}
</style>
