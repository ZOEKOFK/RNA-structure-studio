<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import echarts from '../echarts'
import { api } from '../api'
import { normalizeSequence } from '../sequence'
import RnaArc from '../components/RnaArc.vue'
import FornaCircle from '../components/FornaCircle.vue'

const route = useRoute()
const router = useRouter()

const seq = computed(() => normalizeSequence(route.query.seq || ''))

const fold = ref(null)
const pp = ref(null)
const loading = ref(false)
const error = ref('')
const engineVersion = ref('')
const examples = ref([])
const copied = ref(false)
const heatLimit = ref(300)
const viewMode = ref('circle')
// 超过该长度时圈图（forna）渲染过慢，强制使用弧线图
const FORNA_MAX_LEN = 400
const fornaTooLong = computed(() => seq.value.length > FORNA_MAX_LEN)
const fornaFailed = ref(false)

const currentExample = computed(() => examples.value.find((ex) => ex.sequence === seq.value) || null)

const heatEl = ref(null)
let chart = null

const pairedBases = computed(() => (fold.value ? fold.value.num_pairs * 2 : 0))
const unpairedBases = computed(() => (fold.value ? seq.value.length - pairedBases.value : 0))
const pairedPct = computed(() =>
  seq.value.length ? Math.round((pairedBases.value / seq.value.length) * 100) : 0
)

async function run() {
  if (!seq.value) return
  // 圈图节点数随长度线性增长，1000 nt 需渲染 2200 个节点、耗时接近一分钟；
  // 长序列直接走弧线图，圈图按钮同时禁用，避免用户卡在空白页。
  if (seq.value.length > FORNA_MAX_LEN) viewMode.value = 'arc'
  loading.value = true
  error.value = ''
  fold.value = null
  pp.value = null
  try {
    if (seq.value.length <= heatLimit.value) {
      const [f, p] = await Promise.all([api.fold(seq.value), api.pairprob(seq.value)])
      fold.value = f
      pp.value = p
      await nextTick()
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

const AXIS = {
  line: '#e2e8f0',
  label: '#6b7a90',
  name: '#93a1b5',
  split: '#f1f5f9',
}

function drawHeatmap(matrix) {
  if (!heatEl.value) return
  if (chart && chart.getDom() !== heatEl.value) {
    chart.dispose()
    chart = null
  }
  if (!chart) chart = echarts.init(heatEl.value)
  const n = matrix.length
  const data = []
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const v = matrix[i][j]
      if (v > 0.01) data.push([i, j, v])
    }
  }
  // 位点较多时稀疏显示刻度、隐藏网格线，避免方格噪点
  const labelStep = Math.max(1, Math.ceil(n / 12))
  const dense = n > 40
  // 显式给出类目：热力图数据是稀疏的（只保留 >0.01 的格子），
  // 不指定 data 时 ECharts 会按出现顺序推断类目，导致坐标刻度乱序
  const cats = Array.from({ length: n }, (_, i) => String(i))
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'inherit' },
    tooltip: {
      backgroundColor: '#ffffff',
      borderColor: '#e4e9f2',
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: '#0d1b2f', fontSize: 12 },
      extraCssText: 'box-shadow: 0 12px 30px -12px rgba(13,27,47,.35); border-radius: 10px;',
      formatter: (p) => `位点 ${p.value[0] + 1} – ${p.value[1] + 1}<br/>配对概率 <b>${p.value[2]}</b>`,
    },
    grid: { left: 58, right: 74, top: 34, bottom: 54 },
    xAxis: {
      type: 'category',
      data: cats,
      name: '位点',
      nameLocation: 'middle',
      nameGap: 32,
      splitArea: { show: false },
      axisLine: { lineStyle: { color: AXIS.line } },
      axisTick: { show: false },
      splitLine: { show: !dense, lineStyle: { color: '#f1f5f9', width: 1 } },
      axisLabel: { color: AXIS.label, fontSize: 10, interval: labelStep - 1 },
      nameTextStyle: { color: AXIS.name, fontSize: 11 },
    },
    yAxis: {
      type: 'category',
      data: cats,
      name: '位点',
      nameLocation: 'middle',
      nameGap: 36,
      nameRotate: 90,
      splitArea: { show: false },
      axisLine: { lineStyle: { color: AXIS.line } },
      axisTick: { show: false },
      splitLine: { show: !dense, lineStyle: { color: '#f1f5f9', width: 1 } },
      axisLabel: { color: AXIS.label, fontSize: 10, interval: labelStep - 1 },
      nameTextStyle: { color: AXIS.name, fontSize: 11 },
    },
    visualMap: {
      min: 0,
      max: 1,
      calculable: true,
      orient: 'vertical',
      right: 12,
      top: 'middle',
      itemWidth: 11,
      itemHeight: 108,
      inRange: { color: ['#e8edff', '#a5b4fc', '#6366f1', '#f59e0b'] },
      textStyle: { color: AXIS.label, fontSize: 10 },
    },
    series: [{ type: 'heatmap', data, progressive: 3000, itemStyle: { borderWidth: 0, borderRadius: 1 } }],
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

function onFornaFailed(e) {
  fornaFailed.value = true
  viewMode.value = 'arc'
  console.warn('forna 初始化失败，已回退到弧线图', e)
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
    heatLimit.value = h.pairprob_max_len || heatLimit.value
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
  <div class="page">
    <div v-if="!seq" class="card empty">
      <div class="empty-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M7 3c0 4.5 10 4.5 10 9s-10 4.5-10 9" /><path d="M17 3c0 4.5-10 4.5-10 9s10 4.5 10 9" /></svg>
      </div>
      还没有序列，请先到 <router-link to="/">首页</router-link> 输入一段 RNA。
    </div>

    <template v-else>
      <section class="card">
        <div class="card-head">
          <div>
            <span class="eyebrow">Fold · Step 02</span>
            <h1>二级结构预测</h1>
          </div>
          <div v-if="fold" class="inline">
            <button class="secondary" @click="copyStruct">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><rect x="9" y="9" width="11" height="11" rx="2.5" /><path d="M15 5.5A2.5 2.5 0 0 0 12.5 3H6.5A2.5 2.5 0 0 0 4 5.5v6A2.5 2.5 0 0 0 6.5 14" /></svg>
              {{ copied ? '已复制' : '复制结构' }}
            </button>
            <button class="secondary" @click="downloadResult">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 3v11M8 10l4 4 4-4M4 17v2.5A1.5 1.5 0 0 0 5.5 21h13A1.5 1.5 0 0 0 20 19.5V17" /></svg>
              下载 JSON
            </button>
          </div>
        </div>

        <div class="seq-block">{{ seq }}</div>

        <div v-if="loading" class="loading-line">
          <span class="spinner"></span>
          正在折叠，计算最小自由能与配对概率…
        </div>
        <p v-if="error" class="error">{{ error }}</p>

        <template v-if="fold">
          <div class="metric-grid" style="margin-top: 18px;">
            <div class="metric metric--hero">
              <span class="metric-label">最小自由能 MFE</span>
              <div class="metric-value">
                <span class="mfe">{{ fold.mfe ?? '—' }}</span>
                <small>{{ fold.engine === 'fallback' ? '估计值' : 'kcal/mol' }}</small>
              </div>
              <div class="metric-note">数值越低，结构越稳定</div>
            </div>
            <div class="metric">
              <span class="metric-label">碱基对</span>
              <div class="metric-value">{{ fold.num_pairs }}<small>对</small></div>
              <div class="metric-note">dot-bracket 中的括号数</div>
            </div>
            <div class="metric">
              <span class="metric-label">配对 / 未配对</span>
              <div class="metric-value">{{ pairedBases }}<small>/ {{ unpairedBases }} nt</small></div>
              <div class="pair-meter"><span :style="{ width: pairedPct + '%' }"></span></div>
            </div>
            <div class="metric">
              <span class="metric-label">折叠引擎</span>
              <div class="metric-value" style="font-size: 0.98rem;">
                {{ fold.engine === 'viennarna-python' ? 'ViennaRNA 绑定' : fold.engine === 'viennarna-cli' ? 'ViennaRNA CLI' : '纯 Python 降级' }}
              </div>
              <div class="metric-note">{{ engineVersion ? 'v' + engineVersion : '实时能量模型' }}</div>
            </div>
          </div>
          <p v-if="fold.mfe_note" class="hint">{{ fold.mfe_note }}</p>
        </template>
      </section>

      <section v-if="fold" class="card">
        <div class="card-head">
          <div>
            <h2>二级结构图</h2>
            <p class="hint" style="margin: 2px 0 0;">圈图可拖拽缩放并点击碱基；弧线图便于对照长序列。</p>
          </div>
          <div class="segmented">
            <button
              :class="{ active: viewMode === 'circle' && !fornaFailed }"
              :disabled="fornaFailed || fornaTooLong"
              :title="fornaTooLong ? `序列超过 ${FORNA_MAX_LEN} nt，圈图渲染过慢，已切换为弧线图` : '可拖拽缩放的交互圈图'"
              @click="viewMode = 'circle'"
            >交互圈图</button>
            <button :class="{ active: viewMode === 'arc' }" @click="viewMode = 'arc'">弧线图</button>
          </div>
        </div>

        <p v-if="fornaTooLong" class="hint" style="margin: 0 0 10px;">
          序列 {{ seq.length }} nt 超过 {{ FORNA_MAX_LEN }} nt，交互圈图渲染过慢，已自动切换为弧线图。
        </p>

        <FornaCircle
          v-if="viewMode === 'circle' && !fornaFailed"
          :structure="fold.structure"
          :sequence="seq"
          :height="480"
          @failed="onFornaFailed"
        />
        <RnaArc v-else :structure="fold.structure" :sequence="seq" />
        <p class="hint" v-if="fornaFailed">交互圈图组件加载失败，已自动切换到自绘弧线图。</p>

        <div class="dotline">
          <span class="tag">dot-bracket</span>
          <code class="dotline-code">{{ fold.structure }}</code>
        </div>
        <p v-if="seq.length > heatLimit" class="hint">
          序列 {{ seq.length }} nt 超过配对概率热力图限长 {{ heatLimit }} nt，此处只展示折叠结果。
        </p>
      </section>

      <section v-if="currentExample && currentExample.rfam" class="card">
        <div class="card-head">
          <h2>Rfam 家族注释</h2>
          <span class="tag tag--brand">SEED 对齐</span>
        </div>
        <div class="rfam-grid">
          <div class="metric">
            <span class="metric-label">家族</span>
            <div class="metric-value" style="font-size: 1rem;">
              <a :href="`https://rfam.org/family/${currentExample.rfam.accession}`" target="_blank" rel="noreferrer">
                {{ currentExample.rfam.accession }}
              </a>
            </div>
            <div class="metric-note">{{ currentExample.rfam.name }}</div>
          </div>
          <div class="metric">
            <span class="metric-label">注释</span>
            <div class="metric-note" style="font-size: 0.86rem; margin-top: 2px;">{{ currentExample.annotation }}</div>
          </div>
        </div>

        <div class="consensus">
          <span class="tag">Rfam 共识二级结构</span>
          <p class="mono">{{ currentExample.rfam.consensus }}</p>
        </div>
        <p class="hint">
          共识结构按 Rfam SEED 对齐列给出（{{ currentExample.rfam.consensus.length }} 列），长度与上方预测序列不同；
          <code>&lt; &gt;</code> 表示螺旋、<code>[ ]</code> 表示假结、<code>_ -</code> 表示插入列。可与上方 ViennaRNA 预测对照差异。
        </p>
      </section>

      <section v-if="pp" class="card">
        <div class="card-head">
          <div>
            <h2>碱基配对概率热力图</h2>
            <p class="hint" style="margin: 2px 0 0;">颜色越暖，该位点对在系综中配对概率越高。</p>
          </div>
          <span class="tag tag--teal">
            {{ pp.engine === 'viennarna-bpp' ? '配分函数 bpp' : 'Boltzmann 采样' }}
          </span>
        </div>
        <div ref="heatEl" class="chart-heat"></div>
        <p class="hint">
          引擎：{{ pp.engine === 'viennarna-bpp' ? 'ViennaRNA 配分函数 bpp（真实系综概率）' : 'Boltzmann 采样（降级估计）' }}。
        </p>
      </section>

      <section class="card card--quiet">
        <div class="card-head">
          <h2>快速切换实例</h2>
          <span class="muted">{{ examples.length }} 个预置分子</span>
        </div>
        <div class="example-switch">
          <button
            v-for="ex in examples"
            :key="ex.id"
            class="secondary"
            :class="{ 'is-current': currentExample && currentExample.id === ex.id }"
            @click="loadExample(ex)"
          >{{ ex.name }}</button>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.metric--hero {
  background:
    linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(13, 148, 136, 0.07)),
    var(--surface);
  border-color: rgba(99, 102, 241, 0.2);
}

.chart-heat {
  width: 100%;
  height: 420px;
  border-radius: var(--radius);
  background:
    linear-gradient(180deg, rgba(99, 102, 241, 0.035), transparent 45%),
    #fff;
  border: 1px solid var(--border);
  padding: 6px 2px 2px;
}

.pair-meter {
  margin-top: 8px;
  height: 5px;
  border-radius: 999px;
  background: var(--surface-3);
  overflow: hidden;
}
.pair-meter span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--brand-400), var(--teal));
  transition: width 0.5s var(--ease);
}

.dotline {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
.dotline-code {
  font-family: var(--mono);
  font-size: 0.8rem;
  line-height: 1.6;
  color: var(--text-2);
  /* 长序列（1000 nt）的 dot-bracket 必须能换行，否则 flex 项按内容撑开整页横向滚动 */
  flex: 1 1 0;
  min-width: 0;
  word-break: break-all;
  overflow-wrap: anywhere;
  letter-spacing: 0.06em;
}

.rfam-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.consensus {
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface-2);
}
.consensus p { margin: 8px 0 0; word-break: break-all; line-height: 1.8; }

.example-switch { display: flex; gap: 8px; flex-wrap: wrap; }
.example-switch button {
  min-height: 32px;
  padding: 5px 12px;
  font-size: 0.8rem;
  font-weight: 540;
  border-radius: 9px;
}
.example-switch button.is-current {
  background: var(--brand-soft);
  border-color: rgba(99, 102, 241, 0.3);
  color: var(--brand-600);
  font-weight: 620;
}

@media (max-width: 640px) {
  .rfam-grid { grid-template-columns: 1fr; }
}
</style>
