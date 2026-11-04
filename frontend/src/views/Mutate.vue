<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { normalizeSequence } from '../sequence'
import RnaArc from '../components/RnaArc.vue'

const route = useRoute()

const sequence = ref('')
const position = ref(1)
const base = ref('A')
const result = ref(null)
const loading = ref(false)
const error = ref('')

const bases = ['A', 'U', 'G', 'C']
const maxLen = 300
const deepMaxLen = ref(300)

// 突变结果出来后台自动跑一次 RNA-FM 嵌入偏移，不阻塞页面
const deep = ref(null)
let deepToken = 0
let deepJob = ''

onMounted(async () => {
  sequence.value = normalizeSequence(route.query.seq || '')
  try {
    const h = await api.health()
    if (h.deep_max_len) deepMaxLen.value = h.deep_max_len
  } catch {}
})

onBeforeUnmount(() => {
  deepToken++
  if (deepJob) api.deepCancel(deepJob).catch(() => {})
})

const normalized = computed(() => normalizeSequence(sequence.value))
const len = computed(() => normalized.value.length)

const refBase = computed(() => {
  const p = Number(position.value) - 1
  return normalized.value[p] || '—'
})

const mutatedPreview = computed(() => {
  const s = normalized.value
  const p = Number(position.value) - 1
  if (!s || p < 0 || p >= s.length) return s
  return s.slice(0, p) + base.value + s.slice(p + 1)
})

const highlight = computed(() => (result.value ? [result.value.position - 1] : []))

const trend = computed(() => {
  if (!result.value) return null
  const d = result.value.delta_mfe
  if (d < 0) return { text: '更稳定', cls: 'good', arrow: '↓' }
  if (d > 0) return { text: '更不稳定', cls: 'bad', arrow: '↑' }
  return { text: '能量不变', cls: '', arrow: '→' }
})

async function run() {
  const s = normalized.value
  if (!s || s.length > maxLen) return
  loading.value = true
  error.value = ''
  result.value = null
  stopDeep()
  try {
    result.value = await api.mutate(s, Number(position.value), base.value)
    if (s.length <= deepMaxLen.value) runDeep(s, Number(position.value), base.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function stopDeep() {
  deepToken++
  if (deepJob) {
    api.deepCancel(deepJob).catch(() => {})
    deepJob = ''
  }
  deep.value = null
}

function cancelDeep() {
  stopDeep()
}

async function runDeep(s, pos, b) {
  const token = deepToken
  deep.value = { status: 'queued', stage: '', progress: 0, result: null, error: '' }
  try {
    const { job_id } = await api.deepMutate(s, pos, b)
    if (token !== deepToken) {
      api.deepCancel(job_id).catch(() => {})
      return
    }
    deepJob = job_id
    await pollDeep(job_id, token)
  } catch (e) {
    if (token === deepToken && deep.value) {
      deep.value.status = 'error'
      deep.value.error = e.message
    }
  }
}

async function pollDeep(id, token) {
  const started = Date.now()
  let limit = 120000
  while (token === deepToken) {
    let job
    try {
      job = await api.deepJob(id)
    } catch (e) {
      if (token === deepToken && deep.value) {
        deep.value.status = 'error'
        deep.value.error = e.message
      }
      return
    }
    if (token !== deepToken) return
    deep.value.status = job.status
    deep.value.stage = job.stage || ''
    deep.value.progress = job.progress || 0
    // 首次加载/下载权重时放宽超时上限（与深度页一致）
    if (job.stage) limit = Math.max(limit, 600000)
    if (job.status === 'done') {
      deep.value.result = job.result
      deepJob = ''
      return
    }
    if (job.status === 'error' || job.status === 'cancelled') {
      deep.value.error = job.error || (job.status === 'cancelled' ? '已取消' : '任务失败')
      deepJob = ''
      return
    }
    if (Date.now() - started > limit) {
      deep.value.status = 'error'
      deep.value.error = '深度分析超时'
      api.deepCancel(id).catch(() => {})
      deepJob = ''
      return
    }
    await new Promise((r) => setTimeout(r, 500))
  }
}

const deepShiftAtPosition = computed(() => {
  if (!deep.value || !deep.value.result) return null
  const arr = deep.value.result.shift || []
  return arr[Number(position.value) - 1] ?? null
})

const deepBars = computed(() => {
  if (!deep.value || !deep.value.result) return []
  const r = deep.value.result
  const arr = r.shift || []
  const max = Math.max(...arr, 0.001)
  return (r.top_positions || []).slice(0, 5).map((p) => ({
    position: p,
    value: arr[p - 1],
    width: Math.round(((arr[p - 1] || 0) / max) * 100),
  }))
})
</script>

<template>
  <div class="container">
    <div class="card">
      <h1>突变前后结构对比</h1>
      <p class="muted">输入序列与突变位点，同时折叠野生型/突变型，输出 ΔMFE 与配对集合差异。</p>

      <textarea v-model="sequence" placeholder="输入 RNA 序列…"></textarea>

      <div class="row" style="margin-top: 12px;">
        <div class="col" style="flex: 0 0 150px;">
          <label class="muted">突变位点（1-based）</label>
          <input v-model.number="position" type="number" min="1" :max="len || undefined" />
        </div>
        <div class="col" style="flex: 0 0 120px;">
          <label class="muted">突变碱基</label>
          <select v-model="base">
            <option v-for="b in bases" :key="b" :value="b">{{ b }}</option>
          </select>
        </div>
        <div class="col" style="flex: 0 0 auto; align-self: flex-end;">
          <button :disabled="loading || !normalized.length || normalized.length > maxLen" @click="run">
            运行对比
          </button>
        </div>
      </div>

      <div v-if="len" style="margin-top: 12px;">
        <input
          type="range"
          min="1"
          :max="len"
          v-model.number="position"
          style="width: 100%; padding: 0;"
        />
        <div class="hint">
          位点 <strong>{{ position }}</strong> 当前碱基：<strong>{{ refBase }}</strong>
          → 变为 <strong>{{ base }}</strong>
        </div>
      </div>

      <p v-if="normalized.length > maxLen" class="error">长度超过突变分析限长 {{ maxLen }} nt</p>
      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="normalized" class="hint mono" style="margin-top: 10px;">突变预览：{{ mutatedPreview }}</div>
    </div>

    <template v-if="result">
      <div class="card">
        <div class="row" style="align-items: center;">
          <div class="col">
            <span class="tag">ΔMFE</span><br />
            <span class="mfe">{{ result.delta_mfe }}</span>
            <span v-if="trend" class="trend" :class="trend.cls">{{ trend.arrow }} {{ trend.text }}</span>
          </div>
          <div class="col"><span class="tag">断裂碱基对</span><br />{{ result.broken_pairs.length }}</div>
          <div class="col"><span class="tag">新形成碱基对</span><br />{{ result.formed_pairs.length }}</div>
        </div>
        <p class="hint">ΔMFE &lt; 0 表示突变使结构更稳定（能量更低）。{{ result.note || '' }}</p>
      </div>

      <div class="row">
        <div class="col card">
          <h2>野生型（MFE {{ result.wildtype.mfe }}）</h2>
          <RnaArc :structure="result.wildtype.structure" :sequence="result.sequence" />
          <p class="hint mono">{{ result.wildtype.structure }}</p>
        </div>
        <div class="col card">
          <h2>突变型（MFE {{ result.mutant.mfe }}）</h2>
          <RnaArc :structure="result.mutant.structure" :sequence="result.mutated" :highlight="highlight" />
          <p class="hint mono">{{ result.mutant.structure }}</p>
        </div>
      </div>

      <div class="card">
        <h2>配对差异</h2>
        <div><span class="muted">断裂（野生型有、突变型无）：</span>
          <span v-for="p in result.broken_pairs" :key="'b' + p" class="pair-chip">{{ p[0] + 1 }}–{{ p[1] + 1 }}</span>
          <span v-if="!result.broken_pairs.length" class="muted">无</span>
        </div>
        <div style="margin-top: 8px;"><span class="muted">形成（突变型新增）：</span>
          <span v-for="p in result.formed_pairs" :key="'f' + p" class="pair-chip">{{ p[0] + 1 }}–{{ p[1] + 1 }}</span>
          <span v-if="!result.formed_pairs.length" class="muted">无</span>
        </div>
      </div>
      <div v-if="deep" class="card">
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px;">
          <h2 style="margin: 0;">深度嵌入偏移（RNA-FM）</h2>
          <button v-if="!deep.result && deep.status !== 'error'" class="secondary" @click="cancelDeep">取消</button>
        </div>

        <template v-if="!deep.result">
          <div class="progress"><div class="bar" :style="{ width: Math.round((deep.progress || 0) * 100) + '%' }"></div></div>
          <p class="hint">
            {{ deep.stage || ({ queued: '排队中…', running: '计算中…', cancelled: '已取消', error: '任务失败' }[deep.status] || '准备中…') }}
          </p>
          <p v-if="deep.error" class="error">{{ deep.error }}</p>
        </template>

        <template v-else>
          <div class="row" style="align-items: flex-end;">
            <div class="col">
              <span class="tag">平均位移</span><br />
              <strong>{{ deep.result.mean_shift }}</strong>
            </div>
            <div class="col">
              <span class="tag">突变位点位移</span><br />
              <strong>{{ deepShiftAtPosition ?? '—' }}</strong>
            </div>
            <div class="col">
              <span class="tag">最大位移位点</span><br />
              {{ deep.result.max_shift_position }}（{{ deep.result.max_shift }}）
            </div>
            <div class="col">
              <span class="tag">嵌入</span><br />
              {{ deep.result.engine === 'rna-fm' ? '真实 RNA-FM' : '降级嵌入' }} · {{ deep.result.dim }} 维
            </div>
          </div>

          <div style="margin-top: 12px;">
            <span class="tag">位移最大的 5 个位点</span>
            <div v-for="b in deepBars" :key="b.position" class="bar-row">
              <span class="mono" style="width: 46px;">{{ b.position }}</span>
              <div class="bar-track"><div class="bar-fill" :style="{ width: b.width + '%' }"></div></div>
              <span class="mono" style="width: 54px; text-align: right;">{{ b.value }}</span>
            </div>
          </div>

          <p class="hint">
            位移为每个位点嵌入向量的 L2 距离；数值越大说明该位点在突变后表征变化越明显。
            <template v-if="deep.result.fallback_reason">（{{ deep.result.fallback_reason }}）</template>
          </p>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.trend { margin-left: 8px; font-weight: 600; font-size: 0.9rem; }
.trend.good { color: var(--accent); }
.trend.bad { color: var(--red); }
.progress {
  height: 8px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 999px;
  overflow: hidden;
  margin-top: 10px;
}
.bar {
  height: 100%;
  background: var(--accent);
  transition: width 0.3s ease;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.bar-track {
  flex: 1;
  height: 10px;
  background: var(--panel-2);
  border-radius: 999px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--accent-2, #ffe66d);
}
</style>