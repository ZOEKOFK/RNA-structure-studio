<script setup>
import { computed } from 'vue'

const props = defineProps({
  structure: { type: String, required: true },
  sequence: { type: String, default: '' },
  highlight: { type: Array, default: () => [] },
  legend: { type: Boolean, default: true },
})

const PAIR_COLOR = {
  'GC': '#ff6b6b',
  'CG': '#ff6b6b',
  'AU': '#4ecdc4',
  'UA': '#4ecdc4',
  'GU': '#ffe66d',
  'UG': '#ffe66d',
}

const n = computed(() => props.structure.length)
const step = 13
const baseY = computed(() => props.structure.length * step)

const pairs = computed(() => {
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
    const h = Math.min(14 + (j - i) * 3.2, baseY.value * 0.7)
    return {
      i,
      j,
      color: PAIR_COLOR[type] || '#9aa8bd',
      d: `M ${i * step + step / 2} ${baseY.value} C ${i * step + step / 2} ${baseY.value - h}, ${j * step + step / 2} ${baseY.value - h}, ${j * step + step / 2} ${baseY.value}`,
    }
  })
})

// 每个碱基的着色：配对则按配对类型上色，未配对为灰
const baseColor = computed(() => {
  const arr = new Array(props.structure.length).fill('#55617a')
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
    out.push({ i, text: n.value <= 60 ? (props.sequence[i] || '') : `${i + 1}` })
  }
  return out
})

const height = computed(() => baseY.value * 1.75 + 40)
const width = computed(() => Math.max(n.value * step + 20, 200))
</script>

<template>
  <div>
    <div class="arc-wrap">
      <svg :viewBox="`0 0 ${width} ${height}`" :width="width" :height="height" class="arc-svg">
        <path
          v-for="(p, idx) in pairs"
          :key="idx"
          :d="p.d"
          :stroke="p.color"
          stroke-width="2"
          fill="none"
          stroke-linecap="round"
        />
        <g v-for="k in n" :key="k">
          <circle
            :cx="(k - 1) * step + step / 2"
            :cy="baseY"
            r="3.4"
            :fill="baseColor[k - 1]"
          />
          <circle
            v-if="highlight.includes(k - 1)"
            :cx="(k - 1) * step + step / 2"
            :cy="baseY"
            r="6.5"
            fill="none"
            stroke="#ff8c42"
            stroke-width="2"
          />
        </g>
        <text
          v-for="lb in labels"
          :key="lb.i"
          :x="lb.i * step + step / 2"
          :y="baseY + 16"
          text-anchor="middle"
          :font-size="n <= 60 ? 10 : 9"
          fill="#8b9bb4"
          font-family="ui-monospace, Consolas, monospace"
        >
          {{ lb.text }}
        </text>
      </svg>
    </div>
    <div v-if="legend" class="legend">
      <span><i style="background:#ff6b6b"></i>G–C</span>
      <span><i style="background:#4ecdc4"></i>A–U</span>
      <span><i style="background:#ffe66d"></i>G–U</span>
      <span><i style="background:#55617a"></i>未配对</span>
    </div>
  </div>
</template>

<style scoped>
.arc-wrap { overflow-x: auto; }
.arc-svg { display: block; max-width: 100%; }
.legend {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 6px;
  font-size: 0.82rem;
  color: var(--muted);
}
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
</style>