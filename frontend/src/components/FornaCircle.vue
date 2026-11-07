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

// fornac 在运行时会向 document.head 注入一份全局样式表，其中
// `svg { display:block; width:100%; min-width:100%; min-height:100% }`
// 会把页面上所有内联图标（按钮、徽标里的 svg）撑满容器宽度，导致文字被挤出按钮边框。
// 这里把这份样式表的每条选择器都收进 .forna-wrap 作用域，既保留圈图自身样式，
// 又不影响页面其他 svg。
function scopeFornaStyles() {
  for (const el of document.querySelectorAll('style')) {
    if (el.dataset.fornaScoped) continue
    const css = el.textContent || ''
    if (!css.includes('min-width: 100%') || !css.includes('circle.node')) continue
    el.textContent = css.replace(/(^|\})\s*([^{}@]+?)\s*\{/g, (m, brace, sel) => {
      const scoped = sel
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
        .map((s) => `.forna-wrap ${s}`)
        .join(', ')
      return `${brace}${scoped}{`
    })
    el.dataset.fornaScoped = '1'
  }
}

async function render() {
  if (!host.value || !props.sequence || !props.structure) return
  ready.value = false
  await nextTick()
  try {
    const mod = loadForna()
    scopeFornaStyles()
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
    scopeFornaStyles()
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
    <div v-if="!ready" class="forna-loading">
      <span class="spinner"></span> 圈图渲染中…
    </div>
  </div>
</template>

<style scoped>
.forna-wrap {
  position: relative;
  border: 1px solid var(--border);
  border-radius: var(--radius, 14px);
  overflow: hidden;
  background:
    radial-gradient(80% 70% at 50% 0%, rgba(99, 102, 241, 0.05), transparent 70%),
    #fff;
}
.forna-wrap :deep(svg) {
  cursor: grab;
}
.forna-wrap :deep(svg:active) {
  cursor: grabbing;
}
.forna-wrap :deep(circle.node) {
  stroke: rgba(13, 27, 47, 0.18);
  stroke-width: 1;
}
.forna-wrap :deep(circle.node.label) {
  fill: #f8fafc !important;
  stroke: #cbd5e1 !important;
  stroke-width: 1 !important;
}
.forna-wrap :deep(text) {
  font-family: var(--mono);
  font-weight: 600;
}
.forna-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 0.85rem;
  color: var(--muted);
  background: #fff;
}
</style>
