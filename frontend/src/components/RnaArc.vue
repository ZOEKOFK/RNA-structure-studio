<script setup>
import { computed } from 'vue'

const props = defineProps({
  structure: { type: String, required: true },
  sequence: { type: String, default: '' },
  highlight: { type: Array, default: () => [] },
  legend: { type: Boolean, default: true },
})

// 与全局设计系统的碱基色保持一致（浅色背景下的高对比度版本）
const PAIR_COLOR = {
  'GC': '#e11d48',
  'CG': '#e11d48',
  'AU': '#0d9488',
  'UA': '#0d9488',
  'GU': '#d97706',
  'UG': '#d97706',
}

const BASE_COLOR = {
  A: '#15803d',
  U: '#be123c',
  G: '#b45309',
  C: '#1d4ed8',
}

const n = computed(() => props.structure.length)
const step = 13
const MAX_ARC = 220

// 第一遍：解析配对并计算弧高
const rawPairs = computed(() => {
  const out = []
  const stack = []
  for (let i = 0; i < props.structure.length; i++) {
    if (props.structure[i] === '(') stack.push(i)
    else if (props.structure[i] === ')') {
      if (stack.length) out.push([stack.pop(), i])
    }
  }
  return out.map(([i, j]) => {
    const type = (props.sequence[i] || '') + (props.sequence[j] || '')
    return {
      i,
      j,
      color: PAIR_COLOR[type] || '#94a3b8',
      h: Math.min(16 + (j - i) * 3.2, MAX_ARC),
    }
  })
})

// 画布高度跟随最深的一层配对，避免短序列下方出现大片空白
const maxH = computed(() => rawPairs.value.reduce((m, p) => Math.max(m, p.h), 0))
const baseY = computed(() => Math.round(20 + maxH.value))
const height = computed(() => baseY.value + 36)
const width = computed(() => Math.max(n.value * step + 20, 200))

const pairs = computed(() =>
  rawPairs.value.map((p) => ({
    ...p,
    x1: p.i * step + step / 2,
    x2: p.j * step + step / 2,
    d: `M ${p.i * step + step / 2} ${baseY.value} C ${p.i * step + step / 2} ${baseY.value - p.h}, ${p.j * step + step / 2} ${baseY.value - p.h}, ${p.j * step + step / 2} ${baseY.value}`,
  }))
)

// 每个碱基的着色：配对则按配对类型上色，未配对为中性灰
const baseColor = computed(() => {
  const arr = new Array(props.structure.length).fill('#b9c4d4')
  for (const p of pairs.value) {
    arr[p.i] = p.color
    arr[p.j] = p.color
  }
  return arr
})

const labels = computed(() => {
  const out = []
  const every = n.value <= 60 ? 1 : n.value <= 120 ? 5 : 10
  for (let i = 0; i < n.value; i += every) {
    out.push({
      i,
      text: n.value <= 60 ? (props.sequence[i] || '') : `${i + 1}`,
      color: n.value <= 60 ? BASE_COLOR[props.sequence[i]] || '#94a3b8' : '#93a1b5',
    })
  }
  return out
})
</script>

<template>
  <div>
    <div class="arc-wrap">
      <svg :viewBox="`0 0 ${width} ${height}`" :width="width" :height="height" class="arc-svg">
        <!-- 基线 -->
        <line
          v-if="n"
          :x1="step / 2"
          :y1="baseY"
          :x2="(n - 1) * step + step / 2"
          :y2="baseY"
          stroke="#e2e8f0"
          stroke-width="1.5"
        />
        <path
          v-for="(p, idx) in pairs"
          :key="idx"
          :d="p.d"
          :stroke="p.color"
          stroke-width="2.2"
          stroke-opacity="0.9"
          fill="none"
          stroke-linecap="round"
        />
        <g v-for="k in n" :key="k">
          <circle
            :cx="(k - 1) * step + step / 2"
            :cy="baseY"
            r="3.6"
            :fill="baseColor[k - 1]"
          />
          <circle
            v-if="highlight.includes(k - 1)"
            :cx="(k - 1) * step + step / 2"
            :cy="baseY"
            r="7.5"
            fill="none"
            stroke="#f97316"
            stroke-width="2"
            stroke-dasharray="2 3"
          />
        </g>
        <text
          v-for="lb in labels"
          :key="lb.i"
          :x="lb.i * step + step / 2"
          :y="baseY + 17"
          text-anchor="middle"
          :font-size="n <= 60 ? 10 : 9"
          :fill="lb.color"
          font-weight="600"
          font-family="ui-monospace, Consolas, monospace"
        >
          {{ lb.text }}
        </text>
      </svg>
    </div>
    <div v-if="legend" class="legend">
      <span><i style="background:#e11d48"></i>G–C</span>
      <span><i style="background:#0d9488"></i>A–U</span>
      <span><i style="background:#d97706"></i>G–U</span>
      <span><i style="background:#b9c4d4"></i>未配对</span>
    </div>
  </div>
</template>

<style scoped>
.arc-wrap { overflow-x: auto; padding: 8px 2px 0; }
.arc-svg { display: block; max-width: 100%; margin: 0 auto; }
.legend {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 0.78rem;
  color: var(--muted);
}
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 3px;
}
</style>
