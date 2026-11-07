<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import echarts from '../echarts'
import { api } from '../api'
import { normalizeSequence } from '../sequence'

const limit = ref(300)
const route = useRoute()

const sequence = ref(normalizeSequence(route.query.seq || ''))
const examples = ref([])
const selectedExample = ref('')
const engineState = ref({ engine: 'fallback', error: null })
const engineNote = ref('')

const loading = ref(false)
const progress = ref(0)
const error = ref('')
const embed = ref(null)
const mut = ref(null)
const jobId = ref('')
const queueHint = ref('')
const stageHint = ref('')

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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

async function waitJob(id) {
  jobId.value = id
  const started = Date.now()
  // 首次需要加载/下载约 398 MB 权重，超时上限放宽到 10 分钟
  let timeoutMs = 120000
  try {
    while (Date.now() - started < timeoutMs) {
      const job = await api.deepJob(id)
      progress.value = job.progress || 0
      stageHint.value = job.stage || ''
      if (job.stage) timeoutMs = 600000
      if (job.status === 'queued' && job.queue_position) {
        queueHint.value = `排队中：第 ${job.queue_position} / ${job.queue_length} 位`
      } else {
        queueHint.value = ''
      }
      if (job.status === 'done') return job.result
      if (job.status === 'cancelled') throw new Error('任务已取消')
      if (job.status === 'error') throw new Error(job.error || '任务失败')
      await sleep(250)
    }
  } finally {
    queueHint.value = ''
    stageHint.value = ''
  }
  throw new Error('任务超时，请重试（长序列 CPU 推理可能较慢）')
}

async function cancelJob() {
  if (!jobId.value) return
  try {
    await api.deepCancel(jobId.value)
  } catch {
    // 任务已结束时忽略
  }
}

function downloadJson(payload, filename) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

function exportEmbed() {
  if (embed.value) downloadJson(embed.value, 'rna_embedding.json')
}

function exportMut() {
  if (mut.value) downloadJson(mut.value, 'rna_mutation_embedding.json')
}

async function computeEmbed() {
  const seq = normalizeSequence(sequence.value)
  if (!seq) return
  if (seq.length > limit.value) {
    error.value = `深度分析限长 ${limit.value} nt（当前 ${seq.length} nt）`
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
  const seq = normalizeSequence(sequence.value)
  if (!seq) return
  if (seq.length > limit.value) {
    error.value = `深度分析限长 ${limit.value} nt（当前 ${seq.length} nt）`
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
const AXIS = {
  line: '#e2e8f0',
  label: '#6b7a90',
  name: '#93a1b5',
  split: '#f1f5f9',
}

function axisStyle() {
  return {
    axisLine: { lineStyle: { color: AXIS.line } },
    axisTick: { show: false },
    axisLabel: { color: AXIS.label, fontSize: 10 },
    splitLine: { lineStyle: { color: AXIS.split } },
    nameTextStyle: { color: AXIS.name, fontSize: 11 },
  }
}

function tooltipStyle() {
  return {
    backgroundColor: '#ffffff',
    borderColor: '#e4e9f2',
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: '#0d1b2f', fontSize: 12 },
    extraCssText: 'box-shadow: 0 12px 30px -12px rgba(13,27,47,.35); border-radius: 10px;',
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
    textStyle: { fontFamily: 'inherit' },
    tooltip: { ...tooltipStyle(), formatter: (p) => `${p.data.name}<br/>PC1 ${p.value[0]} / PC2 ${p.value[1]}` },
    grid: { left: 56, right: 42, top: 34, bottom: 46 },
    xAxis: { type: 'value', name: 'PC1', nameLocation: 'middle', nameGap: 28, ...axisStyle() },
    yAxis: { type: 'value', name: 'PC2', nameLocation: 'middle', nameGap: 42, nameRotate: 90, ...axisStyle() },
    series: [
      {
        type: 'line',
        data,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color: '#6366f1', width: 1.2, opacity: 0.5 },
        itemStyle: { color: '#4f46e5', borderColor: '#fff', borderWidth: 1 },
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
    textStyle: { fontFamily: 'inherit' },
    tooltip: { ...tooltipStyle(), trigger: 'axis' },
    grid: { left: 56, right: 42, top: 32, bottom: 50 },
    xAxis: { type: 'category', data: xs, name: '位点', nameLocation: 'middle', nameGap: 28, ...axisStyle(), axisLabel: { color: AXIS.label, fontSize: 10, interval: Math.ceil(xs.length / 20) } },
    yAxis: { type: 'value', name: 'L2 范数', nameLocation: 'middle', nameGap: 44, nameRotate: 90, ...axisStyle() },
    series: [
      {
        type: 'line',
        data: embed.value.norm,
        showSymbol: false,
        smooth: true,
        lineStyle: { color: '#d97706', width: 2 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(217,119,6,0.26)' },
              { offset: 1, color: 'rgba(217,119,6,0.02)' },
            ],
          },
        },
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
    textStyle: { fontFamily: 'inherit' },
    tooltip: {
      ...tooltipStyle(),
      trigger: 'axis',
      formatter: (ps) => `位点 ${ps[0].axisValue}<br/>嵌入偏移 <b>${ps[0].data}</b>`,
    },
    grid: { left: 56, right: 42, top: 32, bottom: 50 },
    xAxis: { type: 'category', data: xs, name: '位点', nameLocation: 'middle', nameGap: 28, ...axisStyle(), axisLabel: { color: AXIS.label, fontSize: 10, interval: Math.ceil(xs.length / 20) } },
    yAxis: { type: 'value', name: 'L2 偏移', nameLocation: 'middle', nameGap: 44, nameRotate: 90, ...axisStyle() },
    visualMap: {
      show: false,
      min: 0,
      max,
      inRange: { color: ['#a5b4fc', '#6366f1', '#f59e0b', '#e11d48'] },
    },
    series: [
      {
        type: 'bar',
        data: mut.value.shift,
        itemStyle: { borderRadius: [2, 2, 0, 0] },
        markLine: {
          symbol: 'none',
          silent: true,
          label: { formatter: '突变位', color: '#e11d48', fontSize: 10 },
          lineStyle: { color: '#e11d48', type: 'dashed', width: 1.2 },
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
    textStyle: { fontFamily: 'inherit' },
    legend: { textStyle: { color: AXIS.label, fontSize: 11 }, top: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10 },
    tooltip: { ...tooltipStyle(), formatter: (p) => `${p.seriesName} ${p.data.name}` },
    grid: { left: 56, right: 42, top: 50, bottom: 46 },
    xAxis: { type: 'value', name: 'PC1', nameLocation: 'middle', nameGap: 28, ...axisStyle() },
    yAxis: { type: 'value', name: 'PC2', nameLocation: 'middle', nameGap: 42, nameRotate: 90, ...axisStyle() },
    series: [
      {
        name: '野生型 WT',
        type: 'line',
        data: mk(mut.value.pca2_wt),
        showSymbol: true,
        symbolSize: 4,
        lineStyle: { color: '#0d9488', width: 1.1, opacity: 0.45 },
        itemStyle: { color: '#0d9488' },
      },
      {
        name: '突变型 MT',
        type: 'line',
        data: mk(mut.value.pca2_mt),
        showSymbol: true,
        symbolSize: 4,
        lineStyle: { color: '#e11d48', width: 1.1, opacity: 0.45 },
        itemStyle: { color: '#e11d48' },
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
  sequence.value = normalizeSequence(ex.sequence)
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
    engineNote.value = h.deep_note || ''
    limit.value = h.deep_max_len || limit.value
    examples.value = e.examples.filter((x) => normalizeSequence(x.sequence).length <= limit.value)
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
  <div class="page">
    <section class="card">
      <div class="card-head">
        <div>
          <span class="eyebrow">Deep · Step 04</span>
          <h1>RNA-FM 深度表征</h1>
        </div>
        <span class="tag" :class="engineState.engine === 'rna-fm' ? 'tag--teal' : 'tag--amber'">
          {{ engineLabel }}
        </span>
      </div>
      <p class="lead">
        逐位置提取 640 维上下文嵌入，观察序列沿 5'→3' 的表征轨迹；
        再做单点突变，量化每个位点的嵌入偏移，定位受影响最大的区域。限长 {{ limit }} nt。
      </p>

      <div class="deep-input">
        <div class="field" style="flex: 1 1 340px;">
          <label>序列</label>
          <textarea
            id="deep-seq"
            v-model="sequence"
            spellcheck="false"
            placeholder="粘贴 RNA 序列，例如 GGGAAACCC…"
            style="min-height: 88px;"
          ></textarea>
        </div>
        <div class="field" style="flex: 0 1 220px;">
          <label>预置示例</label>
          <select v-model="selectedExample" @change="loadExample">
            <option value="">选择示例…</option>
            <option v-for="ex in examples" :key="ex.id" :value="ex.id">{{ ex.name }}</option>
          </select>
          <div class="hint">{{ seqLen }} nt · 逐位置嵌入</div>
        </div>
      </div>

      <div class="actions">
        <button :disabled="loading || !seqLen || seqLen > limit" @click="computeEmbed">
          <span v-if="loading" class="spinner spinner--light"></span>
          {{ loading ? '计算中…' : '计算逐位置嵌入' }}
        </button>
        <button v-if="loading" class="secondary" @click="cancelJob">取消任务</button>
        <span v-if="engineNote" class="hint" style="margin: 0;">{{ engineNote }}</span>
      </div>

      <div v-if="loading" class="progress"><div :style="{ width: (progress * 100) + '%' }"></div></div>
      <p v-if="stageHint" class="hint">{{ stageHint }}</p>
      <p v-else-if="queueHint" class="hint">{{ queueHint }}</p>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="embed" class="metric-grid" style="margin-top: 18px;">
        <div class="metric">
          <span class="metric-label">嵌入维度</span>
          <div class="metric-value">{{ embed.dim }}<small>dim</small></div>
        </div>
        <div class="metric">
          <span class="metric-label">位点数</span>
          <div class="metric-value">{{ embed.length }}<small>nt</small></div>
        </div>
        <div class="metric">
          <span class="metric-label">模型</span>
          <div class="metric-value" style="font-size: 0.9rem;">{{ embed.model }}</div>
        </div>
        <div class="metric">
          <span class="metric-label">结果来源</span>
          <div class="metric-value" style="font-size: 0.9rem;">{{ embed.source === 'cache' ? '缓存命中' : '实时计算' }}</div>
        </div>
        <div class="metric metric--action">
          <button class="secondary" @click="exportEmbed">导出嵌入 JSON</button>
        </div>
      </div>
      <details v-if="embed && embed.fallback_reason" class="fallback-note">
        <summary>当前使用降级嵌入 · 查看原因与启用方法</summary>
        <p>{{ embed.fallback_reason }}</p>
        <p>
          安装 torch + transformers 并将权重放到 <span class="mono">models/rnafm/</span> 后重启，
          即可启用真实 RNA-FM 640 维嵌入。
        </p>
      </details>
    </section>

    <section v-if="embed" class="card">
      <div class="card-head">
        <div>
          <h2>嵌入空间轨迹</h2>
          <p class="hint" style="margin: 2px 0 0;">前两主成分投影，点序对应 5' → 3'。</p>
        </div>
        <span class="tag">PCA 2D</span>
      </div>
      <div ref="pcaEl" class="chart-box" style="height: 360px;"></div>
      <p class="hint">相邻位点在轨迹上的距离，反映嵌入的局部连续性。</p>
    </section>

    <section v-if="embed" class="card">
      <div class="card-head">
        <div>
          <h2>逐位置嵌入强度</h2>
          <p class="hint" style="margin: 2px 0 0;">每个位点嵌入向量的 L2 范数。</p>
        </div>
        <span class="tag">L2 norm</span>
      </div>
      <div ref="normEl" class="chart-box" style="height: 260px;"></div>
    </section>

    <section class="card">
      <div class="card-head">
        <div>
          <h2>突变嵌入偏移</h2>
          <p class="hint" style="margin: 2px 0 0;">比较野生型与突变型的逐位置表征差异。</p>
        </div>
        <span class="tag tag--brand">L2 shift</span>
      </div>

      <div class="mut-controls">
        <div class="field" style="flex: 0 0 120px;">
          <label>位点</label>
          <input v-model.number="position" type="number" min="1" :max="seqLen || 1" />
        </div>
        <div class="field" style="flex: 0 0 110px;">
          <label>突变碱基</label>
          <select v-model="base">
            <option v-for="b in ['A', 'U', 'C', 'G']" :key="b" :value="b">{{ b }}</option>
          </select>
        </div>
        <div class="field" style="flex: 0 0 120px;">
          <label>参考碱基</label>
          <div class="ref-base mono">{{ currentBase || '—' }}</div>
        </div>
        <div class="mut-run">
          <button class="secondary" :disabled="loading || !seqLen" @click="computeMutate">计算偏移</button>
          <button v-if="loading" class="quiet" @click="cancelJob">取消</button>
        </div>
      </div>

      <div v-if="loading" class="progress" style="margin-top: 14px;"><div :style="{ width: (progress * 100) + '%' }"></div></div>
      <p v-if="queueHint && loading" class="hint">{{ queueHint }}</p>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="mut" class="metric-grid" style="margin-top: 16px;">
        <div class="metric">
          <span class="metric-label">平均偏移</span>
          <div class="metric-value">{{ mut.mean_shift }}</div>
        </div>
        <div class="metric">
          <span class="metric-label">最大偏移位点</span>
          <div class="metric-value">{{ mut.max_shift_position }}<small>· {{ mut.max_shift }}</small></div>
        </div>
        <div class="metric">
          <span class="metric-label">受影响 Top5</span>
          <div class="metric-value" style="font-size: 0.9rem;">{{ mut.top_positions.join(', ') }}</div>
        </div>
        <div class="metric">
          <span class="metric-label">嵌入维度</span>
          <div class="metric-value">{{ mut.dim }}<small>dim</small></div>
        </div>
        <div class="metric metric--action">
          <button class="secondary" @click="exportMut">导出偏移 JSON</button>
        </div>
      </div>

      <template v-if="mut">
        <h3 class="chart-title">逐位置嵌入偏移</h3>
        <div ref="shiftEl" class="chart-box" style="height: 280px;"></div>

        <h3 class="chart-title">野生型 / 突变型嵌入空间对比</h3>
        <div ref="mutPcaEl" class="chart-box" style="height: 340px;"></div>

        <div class="struct-pair">
          <div>
            <span class="tag tag--teal">WT</span>
            <div class="seq-block" style="margin-top: 6px;">{{ mut.structure_wt }}</div>
          </div>
          <div>
            <span class="tag tag--rose">MT</span>
            <div class="seq-block" style="margin-top: 6px;">{{ mut.structure_mt }}</div>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.deep-input {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding: 16px;
  border-radius: var(--radius);
  background: var(--surface-2);
  border: 1px solid var(--border);
}
.deep-input textarea { background: #fff; }

.actions { display: flex; gap: 10px; align-items: center; margin-top: 16px; flex-wrap: wrap; }

.metric--action {
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border-style: dashed;
}
.metric--action button { width: 100%; }

.chart-box {
  width: 100%;
  border-radius: var(--radius);
  background:
    linear-gradient(180deg, rgba(99, 102, 241, 0.035), transparent 45%),
    #fff;
  border: 1px solid var(--border);
  padding: 6px 4px 2px;
}
.chart-title { margin: 20px 0 10px; font-size: 0.92rem; }

.mut-controls { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; }
.mut-run { margin-left: auto; display: flex; gap: 6px; align-items: center; }
.ref-base {
  display: flex;
  align-items: center;
  height: 38px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-3);
  border: 1px solid var(--border);
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-2);
}

.struct-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.struct-pair .seq-block { max-height: 120px; }

.spinner--light { border-color: rgba(255, 255, 255, 0.4); border-top-color: #fff; }

.fallback-note {
  margin-top: 14px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  font-size: 0.8rem;
  color: var(--muted);
}
.fallback-note summary {
  cursor: pointer;
  font-weight: 560;
  color: var(--text-2);
  list-style: none;
}
.fallback-note summary::-webkit-details-marker { display: none; }
.fallback-note summary::before {
  content: "＋";
  display: inline-block;
  width: 14px;
  color: var(--brand);
  font-weight: 700;
}
.fallback-note[open] summary::before { content: "－"; }
.fallback-note p { margin: 8px 0 0; line-height: 1.6; word-break: break-word; }

@media (max-width: 700px) {
  .struct-pair { grid-template-columns: 1fr; }
  .mut-run { margin-left: 0; }
}
</style>
