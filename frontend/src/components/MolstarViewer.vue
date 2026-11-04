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

async function load() {
  if (!props.url || !host.value) return
  const token = ++pending
  status.value = 'loading'
  message.value = ''
  try {
    const v = await ensureViewer()
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
    <p v-if="status === 'loading'" class="hint">3D 结构加载中…</p>
    <p v-if="status === 'error'" class="error">
      Mol* 加载失败：{{ message }}
      <a :href="url" target="_blank" rel="noreferrer">下载原始 PDB</a>
    </p>
  </div>
</template>

<style scoped>
.molstar-wrap {
  position: relative;
  border: 1px solid #24324a;
  border-radius: 10px;
  overflow: hidden;
  background: #0b1220;
}
.molstar-wrap :deep(.msp-plugin) {
  height: 100%;
}
</style>
