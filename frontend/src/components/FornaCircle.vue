<script setup>
// forna 交互圈图：直接消费 dot-bracket，可拖拽/缩放/点击碱基。
// 初始化失败（旧浏览器或打包异常）时向父组件抛 failed，由父组件回退到自绘弧线图。
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
// fornac 是 webpack 1 时代的 UMD 包，末尾用 `}(this, factory)` 挂载全局，
// 直接 import 时 `this` 在 ESM 严格模式下是 undefined，会报
// "Cannot read properties of undefined (reading 'document')"。
// 因此以源码字符串形式引入，再用非严格模式的 Function 显式以 window 为 this 执行。
import fornaSource from 'fornac/dist/scripts/fornac.js?raw'

const props = defineProps({
  sequence: { type: String, default: '' },
  structure: { type: String, default: '' },
  height: { type: Number, default: 480 },
})
const emit = defineEmits(['failed'])

const host = ref(null)
const ready = ref(false)
let container = null

function loadForna() {
  if (window.fornac) return window.fornac
  const run = new Function(`${fornaSource}\n;return this.fornac;`)
  const mod = run.call(window)
  if (!mod) throw new Error('fornac 加载失败')
  return mod
}

async function render() {
  if (!host.value || !props.sequence || !props.structure) return
  ready.value = false
  await nextTick()
  try {
    const mod = loadForna()
    const FornaContainer = mod.FornaContainer
    if (typeof FornaContainer !== 'function') throw new Error('fornac 未导出 FornaContainer')

    host.value.innerHTML = ''
    container = new FornaContainer(host.value, {
      applyForce: false,
      editable: false,
      allowPanningAndZooming: true,
      labelInterval: 10,
      initialSize: [Math.max(host.value.clientWidth, 320), props.height],
    })
    container.addRNA(props.structure, { structure: props.structure, sequence: props.sequence })
    if (typeof container.setSize === 'function') container.setSize()
    ready.value = true
  } catch (e) {
    emit('failed', e)
  }
}

watch(() => [props.sequence, props.structure], render)
onMounted(render)
onBeforeUnmount(() => {
  if (host.value) host.value.innerHTML = ''
  container = null
})
</script>

<template>
  <div class="forna-wrap">
    <div ref="host" :style="{ width: '100%', height: height + 'px' }"></div>
    <p v-if="!ready" class="hint">圈图加载中…</p>
  </div>
</template>

<style scoped>
.forna-wrap {
  position: relative;
  border: 1px solid #24324a;
  border-radius: 10px;
  overflow: hidden;
  background: #0f1726;
}
.forna-wrap :deep(svg) {
  cursor: grab;
}
.forna-wrap :deep(.base) {
  font-family: monospace;
}
</style>
