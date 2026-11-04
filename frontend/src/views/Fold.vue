<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { api } from '../api'
import RnaArc from '../components/RnaArc.vue'

const route = useRoute()
const router = useRouter()

const seq = computed(() => {
  const s = (route.query.seq || '').toUpperCase().replace(/T/g, 'U')
  return s.replace(/[^AUCG]/g, '')
})

const fold = ref(null)
const pp = ref(null)
const loading = ref(false)
const error = ref('')
const engineVersion = ref('')
const examples = ref([])
const copied = ref(false)

const heatEl = ref(null)
let chart = null

const pairedBases = computed(() => (fold.value ? fold.value.num_pairs * 2 : 0))
const unpairedBases = computed(() => (fold.value ? seq.value.length - pairedBases.value : 0))

async function run() {
  if (!seq.value) return
  loading.value = true
  error.value = ''
  fold.value = null
  pp.value = null
  try {
    if (seq.value.length <= 200) {
      const [f, p] = await Promise.all([api.fold(seq.value), api.pairprob(seq.value)])
      fold.value = f
      pp.value = p
      drawHeatmap(p.matrix)
    } else {
      fold.value = await api.fold(seq.value)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function drawHeatmap(matrix) {
  if (!heatEl.value) return
  if (!chart) chart = echarts.init(heatEl.value)
  const n = matrix.length
  const data = []
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const v = matrix[i][j]
      if (v > 0.01) data.push([i, j, v])
    }
  }
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (p) => `位点 ${p.value[0] + 1} – ${p.value[1] + 1}<br/>配对概率 ${p.value[2]}`,
    },
    grid: { left: 44, right: 20, top: 24, bottom: 44 },
    xAxis: { type: 'category', name: '位点', splitArea: { show: false }, axisLine: { lineStyle: { color: '#2c3a55' } } },
    yAxis: { type: 'category', name: '位点', splitArea: { show: false }, axisLine: { lineStyle: { color: '#2c3a55' } } },
    visualMap: {
      min: 0,
      max: 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: { color: ['#16233a', '#4ecdc4', '#ffe66d'] },
      textStyle: { color: '#8b9bb4' },
    },
    series: [{ type: 'heatmap', data, progressive: 3000, itemStyle: { borderWidth: 0 } }],
  }, true)
}

async function copyStruct() {
  if (!fold.value) return
  try {
    await navigator.clipboard.writeText(fold.value.structure)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {}
}

function downloadResult() {
  if (!fold.value) return
  const payload = {
    sequence: seq.value,
    structure: fold.value.structure,
    mfe: fold.value.mfe,
    engine: fold.value.engine,
    pairs: fold.value.pairs,
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'rna_fold_result.json'
  a.click()
  URL.revokeObjectURL(a.href)
}

function loadExample(ex) {
  router.push({ path: '/fold', query: { seq: ex.sequence } })
}

function handleResize() {
  chart && chart.resize()
}

onMounted(async () => {
  run()
  window.addEventListener('resize', handleResize)
  try {
    const [h, e] = await Promise.all([api.health(), api.examples()])
    engineVersion.value = h.viennarna_version || ''
    examples.value = e.examples
  } catch {}
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart && chart.dispose()
})

watch(seq, () => run())
</script>

<template>
  <div class="container">
    <div v-if="!seq" class="card">
      <p class="muted">未检测到序列，请先到 <router-link to="/">首页</router-link> 输入。</p>
    </div>

    <template v-else>
      <div class="card">
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
          <h1 style="margin: 0;">二级结构预测</h1>
          <div v-if="fold" style="display: flex; gap: 8px;">
            <button class="secondary" @click="copyStruct">{{ copied ? '已复制 ✓' : '复制 dot-bracket' }}</button>
            <button class="secondary" @click="downloadResult">下载结果</button>
          </div>
        </div>
        <p class="mono hint" style="margin-top: 8px;">{{ seq }}</p>
        <div v-if="loading" class="muted">折叠中…</div>
        <p v-if="error" class="error">{{ error }}</p>

        <template v-if="fold">
          <div class="row" style="align-items: flex-end; margin-top: 8px;">
            <div class="col">
              <span class="tag">最小自由能 MFE</span><br />
              <span class="mfe">{{ fold.mfe ?? '—' }}</span>
              <span class="badge">{{ fold.engine === 'fallback' ? '估计值' : 'kcal/mol' }}</span>
            </div>
            <div class="col">
              <span class="tag">引擎</span><br />
              {{ fold.engine === 'viennarna-python' ? 'ViennaRNA 绑定' : fold.engine === 'viennarna-cli' ? 'ViennaRNA CLI' : '纯 Python 降级' }}
              <span v-if="engineVersion" class="badge">{{ engineVersion }}</span>
            </div>
            <div class="col">
              <span class="tag">碱基对</span><br />{{ fold.num_pairs }}
            </div>
            <div class="col">
              <span class="tag">配对 / 未配对碱基</span><br />{{ pairedBases }} / {{ unpairedBases }}
            </div>
          </div>
          <p v-if="fold.mfe_note" class="hint">{{ fold.mfe_note }}</p>

          <h2 style="margin-top: 18px;">二级结构图</h2>
          <RnaArc :structure="fold.structure" :sequence="seq" />
          <p class="hint mono" style="margin-top: 8px;">{{ fold.structure }}</p>
        </template>
      </div>

      <div v-if="pp" class="card">
        <h2>碱基配对概率热力图</h2>
        <div ref="heatEl" style="width: 100%; height: 420px;"></div>
        <p class="hint">
          引擎：{{ pp.engine === 'viennarna-bpp' ? 'ViennaRNA 配分函数 bpp（真实系综概率）' : 'Boltzmann 采样（降级估计）' }}。
        </p>
      </div>

      <div class="card">
        <h2>快速切换示例</h2>
        <div class="row">
          <div v-for="ex in examples" :key="ex.id" class="col" style="flex: 1 1 150px;">
            <button class="secondary" style="width: 100%;" @click="loadExample(ex)">{{ ex.name }}</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>