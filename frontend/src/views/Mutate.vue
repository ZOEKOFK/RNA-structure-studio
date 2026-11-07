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

// 突变位点上下文（±8 nt），让用户在提交前看到局部环境
const context = computed(() => {
  const s = normalized.value
  const p = Number(position.value) - 1
  if (!s.length || p < 0 || p >= s.length) return null
  const start = Math.max(0, p - 8)
  const end = Math.min(s.length, p + 9)
  return {
    before: s.slice(start, p),
    at: s[p],
    after: s.slice(p + 1, end),
    left: start > 0,
    right: end < s.length,
    index: p + 1,
  }
})

const baseCounts = computed(() => {
  const counts = { A: 0, U: 0, G: 0, C: 0 }
  for (const c of normalized.value) if (counts[c] !== undefined) counts[c]++
  return counts
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
  <div class="page">
    <section class="card">
      <div class="card-head">
        <div>
          <span class="eyebrow">Mutate · Step 03</span>
          <h1>突变前后结构对比</h1>
        </div>
        <span class="tag tag--brand">限长 {{ maxLen }} nt</span>
      </div>
      <p class="lead">
        指定一个位点并替换碱基，平台会同时折叠野生型与突变型，
        给出 ΔMFE、断裂 / 新形成的碱基对，以及深度嵌入的表征偏移。
      </p>

      <div class="mut-input">
        <textarea v-model="sequence" spellcheck="false" placeholder="粘贴 RNA 序列，例如 GGGAAACCC…"></textarea>
      </div>

      <div class="mut-controls">
        <div class="field mut-pos">
          <label>突变位点（1-based）</label>
          <input v-model.number="position" type="number" min="1" :max="len || undefined" />
        </div>

        <div class="field">
          <label>替换为</label>
          <div class="base-picker">
            <button
              v-for="b in bases"
              :key="b"
              type="button"
              class="base-btn"
              :class="['bb-' + b.toLowerCase(), { active: base === b }]"
              @click="base = b"
            >{{ b }}</button>
          </div>
        </div>

        <div class="mut-run">
          <button :disabled="loading || !normalized.length || normalized.length > maxLen" @click="run">
            <span v-if="loading" class="spinner spinner--light"></span>
            {{ loading ? '对比中…' : '运行对比' }}
          </button>
        </div>
      </div>

      <div v-if="len" class="mut-slider">
        <input type="range" min="1" :max="len" v-model.number="position" />
        <div class="slider-meta">
          <span>位点 <strong>{{ position }}</strong> / {{ len }}</span>
          <span class="muted">参考碱基 <strong class="mono">{{ refBase }}</strong> → <strong class="mono">{{ base }}</strong></span>
        </div>
      </div>

      <div v-if="context" class="context-line">
        <span class="context-label">局部上下文</span>
        <span class="context-seq mono">
          <span v-if="context.left" class="ctx-muted">…</span>{{ context.before
          }}<span class="ctx-hit">{{ context.at }}</span>{{ context.after
          }}<span v-if="context.right" class="ctx-muted">…</span>
        </span>
      </div>

      <div v-if="normalized.length" class="base-composition">
        <span v-for="(count, b) in baseCounts" :key="b" class="comp" :class="'bb-' + b.toLowerCase()">
          <i></i>{{ b }} <strong>{{ count }}</strong>
        </span>
      </div>

      <p v-if="normalized.length > maxLen" class="error">长度超过突变分析限长 {{ maxLen }} nt</p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="normalized" class="hint mono" style="margin-top: 12px;">突变预览：{{ mutatedPreview }}</p>
    </section>

    <template v-if="result">
      <section class="delta-card" :class="trend && trend.cls">
        <div class="delta-main">
          <span class="metric-label">ΔMFE · 能量变化</span>
          <div class="delta-value">
            {{ result.delta_mfe }}
            <small>kcal/mol</small>
          </div>
          <span v-if="trend" class="trend-pill" :class="trend.cls">
            {{ trend.arrow }} {{ trend.text }}
          </span>
        </div>
        <div class="delta-split">
          <div class="delta-item">
            <span class="metric-label">断裂碱基对</span>
            <div class="metric-value">{{ result.broken_pairs.length }}<small>对</small></div>
            <div class="metric-note">野生型有、突变型无</div>
          </div>
          <div class="delta-item">
            <span class="metric-label">新形成碱基对</span>
            <div class="metric-value">{{ result.formed_pairs.length }}<small>对</small></div>
            <div class="metric-note">突变后新增的配对</div>
          </div>
        </div>
        <p class="delta-note">
          ΔMFE &lt; 0 表示突变使结构更稳定（能量更低）。{{ result.note || '' }}
        </p>
      </section>

      <div class="compare">
        <section class="card compare-card">
          <div class="card-head">
            <h2><span class="dot dot--wt"></span>野生型</h2>
            <span class="tag">MFE {{ result.wildtype.mfe }}</span>
          </div>
          <RnaArc :structure="result.wildtype.structure" :sequence="result.sequence" :legend="false" />
          <div class="seq-block" style="margin-top: 10px;">{{ result.wildtype.structure }}</div>
        </section>

        <section class="card compare-card">
          <div class="card-head">
            <h2><span class="dot dot--mt"></span>突变型</h2>
            <span class="tag tag--rose">MFE {{ result.mutant.mfe }}</span>
          </div>
          <RnaArc :structure="result.mutant.structure" :sequence="result.mutated" :highlight="highlight" :legend="false" />
          <div class="seq-block" style="margin-top: 10px;">{{ result.mutant.structure }}</div>
        </section>
      </div>

      <section class="card">
        <div class="card-head">
          <h2>配对差异</h2>
          <span class="muted">位点编号从 1 开始</span>
        </div>
        <div class="diff-row">
          <span class="tag tag--rose">断裂</span>
          <span v-for="p in result.broken_pairs" :key="'b' + p" class="pair-chip pair-chip--broken">{{ p[0] + 1 }}–{{ p[1] + 1 }}</span>
          <span v-if="!result.broken_pairs.length" class="muted">无</span>
        </div>
        <div class="diff-row">
          <span class="tag tag--teal">形成</span>
          <span v-for="p in result.formed_pairs" :key="'f' + p" class="pair-chip pair-chip--formed">{{ p[0] + 1 }}–{{ p[1] + 1 }}</span>
          <span v-if="!result.formed_pairs.length" class="muted">无</span>
        </div>
      </section>

      <section v-if="deep" class="card">
        <div class="card-head">
          <div>
            <h2>深度嵌入偏移 · RNA-FM</h2>
            <p class="hint" style="margin: 2px 0 0;">后台自动计算，不阻塞上面的结构对比。</p>
          </div>
          <button v-if="!deep.result && deep.status !== 'error'" class="quiet" @click="cancelDeep">取消</button>
        </div>

        <template v-if="!deep.result">
          <div class="progress"><div class="bar" :style="{ width: Math.round((deep.progress || 0) * 100) + '%' }"></div></div>
          <div class="loading-line">
            <span class="spinner"></span>
            {{ deep.stage || ({ queued: '排队中…', running: '计算中…', cancelled: '已取消', error: '任务失败' }[deep.status] || '准备中…') }}
          </div>
          <p v-if="deep.error" class="error">{{ deep.error }}</p>
        </template>

        <template v-else>
          <div class="metric-grid">
            <div class="metric">
              <span class="metric-label">平均位移</span>
              <div class="metric-value">{{ deep.result.mean_shift }}</div>
            </div>
            <div class="metric">
              <span class="metric-label">突变位点位移</span>
              <div class="metric-value">{{ deepShiftAtPosition ?? '—' }}</div>
            </div>
            <div class="metric">
              <span class="metric-label">最大位移位点</span>
              <div class="metric-value">{{ deep.result.max_shift_position }}<small>· {{ deep.result.max_shift }}</small></div>
            </div>
            <div class="metric">
              <span class="metric-label">嵌入</span>
              <div class="metric-value" style="font-size: 0.95rem;">
                {{ deep.result.engine === 'rna-fm' ? '真实 RNA-FM' : '降级嵌入' }}
              </div>
              <div class="metric-note">{{ deep.result.dim }} 维向量</div>
            </div>
          </div>

          <div class="shift-block">
            <span class="metric-label">位移最大的 5 个位点</span>
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
      </section>
    </template>
  </div>
</template>

<style scoped>
.mut-input {
  margin-top: 16px;
  padding: 4px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface-2);
}
.mut-input textarea {
  border: none;
  background: transparent;
  box-shadow: none;
  min-height: 96px;
  padding: 10px 12px;
}
.mut-input:focus-within { border-style: solid; border-color: rgba(99, 102, 241, 0.35); background: #fff; }
.mut-input textarea:focus { box-shadow: none; background: transparent; }

.mut-controls {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  flex-wrap: wrap;
  margin-top: 16px;
}
.mut-pos { flex: 0 0 150px; }
.mut-run { margin-left: auto; }

.base-picker {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  background: var(--surface-3);
  border: 1px solid var(--border);
  border-radius: 11px;
}
.base-btn {
  min-width: 38px;
  min-height: 32px;
  padding: 0 10px;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
  font-family: var(--mono);
  font-size: 0.86rem;
  font-weight: 700;
  border: 1px solid transparent;
}
.base-btn:hover:not(:disabled) { transform: none; box-shadow: none; background: rgba(255, 255, 255, 0.8); }
.base-btn.active { background: #fff; box-shadow: 0 1px 3px rgba(13, 27, 47, 0.12); }
.bb-a.active { color: var(--base-a); }
.bb-u.active { color: var(--base-u); }
.bb-g.active { color: var(--base-g); }
.bb-c.active { color: var(--base-c); }

.mut-slider { margin-top: 18px; }
.slider-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 0.8rem;
  color: var(--muted);
  margin-top: 4px;
}
.slider-meta strong { color: var(--text); }

.context-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px solid var(--border);
}
.context-label {
  font-size: 0.7rem;
  font-weight: 620;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted-2);
}
.context-seq { font-size: 0.88rem; letter-spacing: 0.12em; color: var(--muted); }
.ctx-hit {
  display: inline-block;
  padding: 1px 5px;
  margin: 0 1px;
  border-radius: 5px;
  background: var(--brand-soft);
  color: var(--brand-600);
  font-weight: 700;
}
.ctx-muted { color: var(--muted-2); }

.base-composition { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 12px; }
.comp {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.comp i { width: 8px; height: 8px; border-radius: 3px; display: inline-block; }
.comp strong { color: var(--text-2); }
.comp.bb-a i { background: var(--base-a); }
.comp.bb-u i { background: var(--base-u); }
.comp.bb-g i { background: var(--base-g); }
.comp.bb-c i { background: var(--base-c); }

/* ---------- ΔMFE 结果卡 ---------- */
.delta-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 26px;
  flex-wrap: wrap;
  padding: 22px 26px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
  margin-bottom: 18px;
  overflow: hidden;
}
.delta-card::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(120deg, rgba(99, 102, 241, 0.1), transparent 60%);
  pointer-events: none;
}
.delta-card.good { border-color: rgba(13, 148, 136, 0.35); }
.delta-card.good::before { background: linear-gradient(120deg, rgba(13, 148, 136, 0.13), transparent 62%); }
.delta-card.bad { border-color: rgba(225, 29, 72, 0.3); }
.delta-card.bad::before { background: linear-gradient(120deg, rgba(225, 29, 72, 0.1), transparent 62%); }

.delta-main { position: relative; min-width: 190px; }
.delta-value {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 2.4rem;
  font-weight: 740;
  letter-spacing: -0.04em;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  margin-top: 2px;
}
.delta-value small { font-size: 0.8rem; font-weight: 520; color: var(--muted); letter-spacing: 0; }
.trend-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 620;
  background: var(--surface-3);
  border: 1px solid var(--border);
  color: var(--muted);
}
.trend-pill.good { background: var(--teal-soft); border-color: #99f6e4; color: #0f766e; }
.trend-pill.bad { background: var(--rose-soft); border-color: #fecdd3; color: #be123c; }

.delta-split {
  position: relative;
  display: flex;
  gap: 12px;
  flex: 1;
  flex-wrap: wrap;
}
.delta-item {
  flex: 1 1 150px;
  padding: 12px 14px;
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--border);
  backdrop-filter: blur(4px);
}
.delta-note {
  position: relative;
  flex-basis: 100%;
  font-size: 0.8rem;
  color: var(--muted);
  margin: 0;
  padding-top: 4px;
  border-top: 1px dashed var(--border);
}

/* ---------- 对比双栏 ---------- */
.compare { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.compare-card { margin-bottom: 0; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 8px; vertical-align: 1px; }
.dot--wt { background: var(--teal); box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.15); }
.dot--mt { background: var(--rose); box-shadow: 0 0 0 3px rgba(225, 29, 72, 0.13); }
.compare-card h2 { display: inline-flex; align-items: center; }

.diff-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 0;
}
.diff-row + .diff-row { border-top: 1px dashed var(--border); }

.shift-block { margin-top: 18px; }
.spinner--light { border-color: rgba(255, 255, 255, 0.4); border-top-color: #fff; }

@media (max-width: 860px) {
  .compare { grid-template-columns: 1fr; }
  .mut-run { margin-left: 0; }
  .delta-card { gap: 18px; }
}
</style>
