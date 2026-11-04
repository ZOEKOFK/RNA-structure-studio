<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { api } from '../api'

const LIMIT = 300
const route = useRoute()

const sequence = ref(normalize(route.query.seq || ''))
const examples = ref([])
const selectedExample = ref('')
const engineState = ref({ engine: 'fallback', error: null })

const loading = ref(false)
const progress = ref(0)
const error = ref('')
const embed = ref(null)
const mut = ref(null)

const position = ref(1)
const base = ref('A')

const pcaEl = ref(null)
const normEl = ref(null)
const shiftEl = ref(null)
const mutPcaEl = ref(null)
const charts = { pca: null, norm: null, shift: null, mutPca: null }

const seqLen = computed(() => sequence.value.length)
const currentBase = computed(() => sequence.value[position.value - 1] || '')
const engineLabel = computed(() =>
  engineState.value.engine === 'rna-fm' ? 'RNA-FM（真实深度模型）' : '序列+结构特征（降级嵌入）'
)

function normalize(s) {
  return (s || '').toUpperCase().replace(/T/g, 'U').replace(/[^AUCG]/g, '')
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

async function waitJob(id) {
  const started = Date.now()
  while (Date.now() - started < 60000) {
    const job = await api.deepJob(id)
    progress.value = job.progress || 0
    if (job.status === 'done') return job.result
    if (job.status === 'error') throw new Error(job.error || '任务失败')
    await sleep(250)
  }
  throw new Error('任务超时，请重试（长序列 CPU 推理可能较慢）')
}

async function computeEmbed() {
  const seq = normalize(sequence.value)
  if (!seq) return
  if (seq.length > LIMIT) {
    error.value = `深度分析限长 ${LIMIT} nt（当前 ${seq.length} nt）`
    return
  }
  loading.value = true
  error.value = ''
  embed.value = null
  mut.value = null
  progress.value = 0
  try {
    const { job_id } = await api.deepEmbed(seq)
    embed.value = await waitJob(job_id)
    await nextTick()
    drawPca()
    drawNorm()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function computeMutate() {
  const seq = normalize(sequence.value)
  if (!seq) return
  if (seq.length > LIMIT) {
    error.value = `深度分析限长 ${LIMIT} nt（当前 ${seq.length} nt）`
    return
  }
  if (position.value < 1 || position.value > seq.length) {
    error.value = '突变位点超出序列范围'
    return
  }
  loading.value = true
  error.value = ''
  mut.value = null
  progress.value = 0
  try {
    const { job_id } = await api.deepMutate(seq, position.value, base.value)
    mut.value = await waitJob(job_id)
    await nextTick()
    drawShift()
    drawMutPca()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// ---------------- charts ----------------
function axisStyle() {
  return {
    axisLine: { lineStyle: { color: '#2c3a55' } },
    axisLabel: { color: '#8b9bb4' },
    splitLine: { lineStyle: { color: '#1c2940' } },
    nameTextStyle: { color: '#8b9bb4' },
  }
}

function chartFor(key, el) {
  if (!el) return null
  const existing = charts[key]
  if (existing && existing.getDom() === el) return existing
  if (existing) {
    try {
      existing.dispose()
    } catch {}
    charts[key] = null
  }
  charts[key] = echarts.init(el)
  return charts[key]
}

function drawPca() {
  const c = chartFor('pca', pcaEl.value)
  if (!c || !embed.value) return
  const seq = embed.value.sequence
  const data = embed.value.pca2.map((p, i) => ({
    value: p,
    name: `${i + 1} · ${seq[i]}`,
  }))
  c.setOption({
    backgroundColor: 'transparent',
    tooltip: { formatter: (p) => `${p.data.name}<br/>PC1 ${p.value[0]} / PC2 ${p.value[1]}` },
    grid: { left: 56, right: 24, top: 28, bottom: 40 },
    xAxis: { type: 'value', name: 'PC1', ...axisStyle() },
    yAxis: { type: 'value', name: 'PC2', ...axisStyle() },
    series: [
      {
        type: 'line',
        data,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color: '#4ecdc4', width: 1, opacity: 0.55 },
        itemStyle: { color: '#4ecdc4' },
      },
    ],
  }, true)
}

function drawNorm() {
  const c = chartFor('norm', normEl.value)
  if (!c || !embed.value) return
  const xs = embed.value.norm.map((_, i) => String(i + 1))
  c.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 24, top: 24, bottom: 44 },
    xAxis: { type: 'category', data: xs, name: '位点', ...axisStyle(), axisLabel: { color: '#8b9bb4', interval: Math.ceil(xs.length / 20) } },
    yAxis: { type: 'value', name: 'L2 范数', ...axisStyle() },
    series: [
      {
        type: 'line',
        data: embed.value.norm,
        showSymbol: false,
        smooth: true,
        lineStyle: { color: '#ffe66d', width: 1.6 },
        areaStyle: { color: 'rgba(255, 230, 109, 0.12)' },
      },
    ],
  }, true)
}

function drawShift() {
  const c = chartFor('shift', shiftEl.value)
  if (!c || !mut.value) return
  const xs = mut.value.shift.map((_, i) => String(i + 1))
  const max = Math.max(...mut.value.shift, 0.001)
  c.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => `位点 ${ps[0].axisValue}<br/>嵌入偏移 ${ps[0].data}`,
    },
    grid: { left: 56, right: 24, top: 24, bottom: 44 },
    xAxis: { type: 'category', data: xs, name: '位点', ...axisStyle(), axisLabel: { color: '#8b9bb4', interval: Math.ceil(xs.length / 20) } },
    yAxis: { type: 'value', name: 'L2 偏移', ...axisStyle() },
    visualMap: {
      show: false,
      min: 0,
      max,
      inRange: { color: ['#2b6e6b', '#4ecdc4', '#ffe66d', '#ff6b6b'] },
    },
    series: [
      {
        type: 'bar',
        data: mut.value.shift,
        markLine: {
          symbol: 'none',
          silent: true,
          label: { formatter: '突变位', color: '#ff6b6b' },
          lineStyle: { color: '#ff6b6b', type: 'dashed' },
          data: [{ xAxis: mut.value.position - 1 }],
        },
      },
    ],
  }, true)
}

function drawMutPca() {
  const c = chartFor('mutPca', mutPcaEl.value)
  if (!c || !mut.value) return
  const seq = mut.value.sequence
  const mk = (pts) =>
    pts.map((p, i) => ({ value: p, name: `${i + 1} · ${seq[i]}` }))
  c.setOption({
    backgroundColor: 'transparent',
    legend: { textStyle: { color: '#8b9bb4' }, top: 0 },
    tooltip: { formatter: (p) => `${p.seriesName} ${p.data.name}` },
    grid: { left: 56, right: 24, top: 44, bottom: 40 },
    xAxis: { type: 'value', name: 'PC1', ...axisStyle() },
    yAxis: { type: 'value', name: 'PC2', ...axisStyle() },
    series: [
      {
        name: '野生型 WT',
        type: 'line',
        data: mk(mut.value.pca2_wt),
        showSymbol: true,
        symbolSize: 4,
        lineStyle: { color: '#4ecdc4', width: 1, opacity: 0.5 },
        itemStyle: { color: '#4ecdc4' },
      },
      {
        name: '突变型 MT',
        type: 'line',
        data: mk(mut.value.pca2_mt),
        showSymbol: true,
        symbolSize: 4,
        lineStyle: { color: '#ff6b6b', width: 1, opacity: 0.5 },
        itemStyle: { color: '#ff6b6b' },
      },
    ],
  }, true)
}

function handleResize() {
  Object.values(charts).forEach((c) => c && c.resize())
}

function loadExample() {
  const ex = examples.value.find((e) => e.id === selectedExample.value)
  if (!ex) return
  sequence.value = normalize(ex.sequence)
  position.value = 1
  base.value = 'A'
  embed.value = null
  mut.value = null
}

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  try {
    const [h, e] = await Promise.all([api.health(), api.examples()])
    engineState.value = { engine: h.deep_engine || 'fallback', error: h.deep_error || null }
    examples.value = e.examples.filter((x) => normalize(x.sequence).length <= LIMIT)
  } catch {
    error.value = '后端未启动，请先运行 uvicorn backend.main:app --port 8000'
  }
  if (seqLen.value) computeEmbed()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  Object.values(charts).forEach((c) => c && c.dispose())
})
</script>

<template>
  <div class="container">
    <div class="card">
      <h1>RNA-FM 深度分析</h1>
      <p class="muted" style="margin-top: 0;">
        引擎：<strong>{{ engineLabel }}</strong>。逐位置嵌入 + 突变嵌入偏移，限长 {{ LIMIT }} nt。
      </p>

      <div class="row" style="align-items: flex-end;">
        <div class="col" style="flex: 1 1 320px;">
          <span class="tag">序列</span>
          <textarea
            id="deep-seq"
            v-model="sequence"
            placeholder="粘贴 RNA 序列，例如 GGGAAACCC…"
            style="min-height: 84px;"
          ></textarea>
        </div>
        <div class="col" style="flex: 0 0 220px;">
          <span class="tag">预置示例</span>
          <select v-model="selectedExample" @change="loadExample">
            <option value="">选择示例…</option>
            <option v-for="ex in examples" :key="ex.id" :value="ex.id">{{ ex.name }}</option>
          </select>
        </div>
      </div>

      <div class="hint">{{ seqLen }} nt</div>

      <div class="actions">
        <button :disabled="loading || !seqLen || seqLen > LIMIT" @click="computeEmbed">
          {{ loading ? '计算中…' : '计算逐位置嵌入' }}
        </button>
      </div>

      <div v-if="loading" class="progress"><div :style="{ width: (progress * 100) + '%' }"></div></div>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="embed" class="row stats">
        <div class="col"><span class="tag">嵌入维度</span><br />{{ embed.dim }}</div>
        <div class="col"><span class="tag">位点数</span><br />{{ embed.length }}</div>
        <div class="col"><span class="tag">模型</span><br />{{ embed.model }}</div>
        <div class="col"><span class="tag">结果来源</span><br />{{ embed.source === 'cache' ? '缓存' : '实时计算' }}</div>
      </div>
      <p v-if="embed && embed.fallback_reason" class="hint">
        降级原因：{{ embed.fallback_reason }}。安装 torch + transformers 并将权重放到
        <span class="mono">models/rnafm/</span> 后重启即可启用真实 RNA-FM。
      </p>
    </div>

    <div v-if="embed" class="card">
      <h2>嵌入空间轨迹（前两主成分）</h2>
      <div ref="pcaEl" style="width: 100%; height: 360px;"></div>
      <p class="hint">点按轨迹顺序对应 5' → 3'，相邻位点距离反映嵌入的局部连续性。</p>
    </div>

    <div v-if="embed" class="card">
      <h2>逐位置嵌入强度</h2>
      <div ref="normEl" style="width: 100%; height: 260px;"></div>
    </div>

    <div class="card">
      <h2>突变嵌入偏移</h2>
      <div class="row" style="align-items: flex-end;">
        <div class="col" style="flex: 0 0 120px;">
          <span class="tag">位点</span>
          <input v-model.number="position" type="number" min="1" :max="seqLen || 1" />
        </div>
        <div class="col" style="flex: 0 0 120px;">
          <span class="tag">突变碱基</span>
          <select v-model="base">
            <option v-for="b in ['A', 'U', 'C', 'G']" :key="b" :value="b">{{ b }}</option>
          </select>
        </div>
        <div class="col"><span class="tag">参考碱基</span><br /><span class="mono">{{ currentBase || '—' }}</span></div>
        <div class="col" style="flex: 0 0 auto;">
          <button class="secondary" :disabled="loading || !seqLen" @click="computeMutate">计算偏移</button>
        </div>
      </div>

      <div v-if="loading" class="progress" style="margin-top: 12px;"><div :style="{ width: (progress * 100) + '%' }"></div></div>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="mut" class="row stats">
        <div class="col"><span class="tag">平均偏移</span><br />{{ mut.mean_shift }}</div>
        <div class="col"><span class="tag">最大偏移位点</span><br />{{ mut.max_shift_position }}（{{ mut.max_shift }}）</div>
        <div class="col"><span class="tag">受影响 Top5</span><br />{{ mut.top_positions.join(', ') }}</div>
        <div class="col"><span class="tag">嵌入维度</span><br />{{ mut.dim }}</div>
      </div>

      <template v-if="mut">
        <h3 style="margin-top: 18px;">逐位置嵌入偏移</h3>
        <div ref="shiftEl" style="width: 100%; height: 280px;"></div>

        <h3 style="margin-top: 18px;">野生型 / 突变型嵌入空间对比</h3>
        <div ref="mutPcaEl" style="width: 100%; height: 340px;"></div>

        <p class="hint mono" style="margin-top: 10px;">WT: {{ mut.structure_wt }}</p>
        <p class="hint mono">MT: {{ mut.structure_mt }}</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.actions { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
.stats { margin-top: 14px; gap: 16px; }
.progress {
  height: 6px;
  margin-top: 10px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.progress > div { height: 100%; background: var(--accent); transition: width 0.2s linear; }
textarea { min-height: 72px; }
</style>
