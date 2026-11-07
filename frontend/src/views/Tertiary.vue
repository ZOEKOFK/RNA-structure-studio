<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { normalizeSequence } from '../sequence'
import MolstarViewer from '../components/MolstarViewer.vue'

const route = useRoute()
const sequence = ref('')
const examples = ref([])
const result = ref(null)
const loading = ref(false)
const error = ref('')
const selectedPdb = ref('')
const copied = ref(false)
const controller = ref(null)
// 本地 3D 建模（内置简化建模 / 外部引擎钩子）
const localModel = ref(null)
const localJobId = ref('')
const localStage = ref('')
const localError = ref('')
const building = ref(false)
const localTimer = ref(null)
const localToken = ref(0)
// 本地建模限长从 /api/health 读取（后端可用 RNA_MAX_3D_LEN 调整），默认 500
const maxLen3d = ref(500)
// 伪结等建模注意事项（提交时由后端返回）
const localNote = ref('')

const normalized = computed(() => normalizeSequence(sequence.value))
const over3dLimit = computed(() => normalized.value.length > maxLen3d.value)

onMounted(async () => {
  const s = normalizeSequence(route.query.seq || '')
  try {
    const e = await api.examples()
    examples.value = e.examples
  } catch (e) {
    error.value = '后端未启动，请先运行 uvicorn backend.main:app --port 8000'
  }
  try {
    const h = await api.health()
    if (h && h.model_3d_max_len) maxLen3d.value = h.model_3d_max_len
  } catch (e) {
    /* 后端未启动时保持默认 500，与按钮提示一致 */
  }
  if (s) {
    sequence.value = s
    run()
  }
})

function fill(ex) {
  sequence.value = ex.sequence
  result.value = null
  error.value = ''
  // 预置结构库均有 PDB，直接查询并展示
  run()
}

const viewerPdb = computed(
  () => selectedPdb.value || (result.value && result.value.pdb) || ''
)

// 本地已收录坐标优先（离线可用），否则回退到 RCSB 公开下载地址
const viewerLocal = computed(() => {
  if (!viewerPdb.value) return false
  const hit = ((result.value && result.value.similar) || []).find((x) => x.pdb_id === viewerPdb.value)
  return Boolean((result.value && result.value.pdb_local) || (hit && hit.local))
})

const viewerUrl = computed(() => {
  if (!viewerPdb.value) return ''
  return viewerLocal.value
    ? `/api/pdb/${viewerPdb.value}`
    : `https://files.rcsb.org/download/${viewerPdb.value}.pdb`
})

onUnmounted(() => {
  if (controller.value) controller.value.abort()
  cancelLocal()
})

function pickSimilar(item) {
  selectedPdb.value = item.pdb_id
}

function resolutionText(res) {
  if (Array.isArray(res)) return res.length ? `${res[0]} Å` : '未知'
  return res ? `${res} Å` : '未知'
}

function scoreText(score) {
  return score == null ? '' : `相似度 ${(score * 100).toFixed(0)}%`
}

async function copyBridge() {
  const text = result.value && result.value.rnacomposer && result.value.rnacomposer.input
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch (e) {
    // 非 https / 旧浏览器降级：选中隐藏 textarea 后 execCommand
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function cancelLocal() {
  if (localTimer.value) {
    clearTimeout(localTimer.value)
    localTimer.value = null
  }
  const id = localJobId.value
  localJobId.value = ''
  building.value = false
  localToken.value += 1
  if (id) api.model3dCancel(id).catch(() => {})
}

async function buildLocal() {
  if (!normalized.value || over3dLimit.value) return
  cancelLocal()
  const token = localToken.value
  localError.value = ''
  localModel.value = null
  localNote.value = ''
  building.value = true
  localStage.value = '提交任务…'
  try {
    const r = await api.model3d(normalized.value, (result.value && result.value.structure) || '')
    if (token !== localToken.value) return
    localNote.value = r.note || ''
    localJobId.value = r.job_id
    localStage.value = '排队中…'
    pollLocal(r.job_id, token)
  } catch (e) {
    if (token !== localToken.value) return
    building.value = false
    localError.value = e.message
  }
}

function pollLocal(id, token) {
  localTimer.value = setTimeout(async () => {
    if (token !== localToken.value) return
    try {
      const job = await api.model3dJob(id)
      if (token !== localToken.value) return
      localStage.value = job.stage || job.status
      if (job.status === 'done') {
        localModel.value = job.result
        localJobId.value = ''
        localTimer.value = null
        building.value = false
        return
      }
      if (job.status === 'error' || job.status === 'cancelled') {
        localError.value = job.error || '任务已取消'
        localJobId.value = ''
        localTimer.value = null
        building.value = false
        return
      }
      pollLocal(id, token)
    } catch (e) {
      if (token !== localToken.value) return
      localError.value = e.message
      localJobId.value = ''
      localTimer.value = null
      building.value = false
    }
  }, 500)
}

async function run() {
  if (!normalized.value) return
  cancel()
  cancelLocal()
  localModel.value = null
  localError.value = ''
  const c = new AbortController()
  controller.value = c
  loading.value = true
  error.value = ''
  result.value = null
  selectedPdb.value = ''
  copied.value = false
  try {
    result.value = await api.tertiary(normalized.value, { signal: c.signal })
  } catch (e) {
    // 用户主动取消时不当作错误展示
    if (!c.signal.aborted) error.value = e.message
  } finally {
    if (controller.value === c) {
      controller.value = null
      loading.value = false
    }
  }
}

function cancel() {
  if (controller.value) {
    controller.value.abort()
    controller.value = null
    loading.value = false
  }
}
</script>

<template>
  <div class="page">
    <section class="card">
      <div class="card-head">
        <div>
          <span class="eyebrow">Structure · 3D</span>
          <h1>三维结构查看</h1>
        </div>
        <span class="tag tag--brand">Mol* Viewer</span>
      </div>
      <p class="lead">
        先比对预置结构库；未命中时用 RCSB PDB 检索序列相似度 ≥90% 的已解析实验结构；
        都没有时可本地生成离线简化模型，或用 RNAComposer 手动预测。
      </p>

      <div class="ter-input">
        <textarea v-model="sequence" spellcheck="false" placeholder="输入 RNA 序列，或点击下方预置结构…"></textarea>
      </div>

      <div class="ter-actions">
        <button :disabled="loading || !normalized.length" @click="run">
          <span v-if="loading" class="spinner spinner--light"></span>
          {{ loading ? '查询中…' : '查询 3D 结构' }}
        </button>
        <button v-if="loading" class="secondary" @click="cancel">取消查询</button>
        <span class="hint" style="margin: 0;">{{ normalized.length }} nt</span>
      </div>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="loading" class="loading-line">
        <span class="spinner"></span>
        正在比对预置库并检索 RCSB（外部网络可能较慢）…
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <div>
          <h2>预置结构库</h2>
          <p class="hint" style="margin: 2px 0 0;">坐标为本地收录，断网也能渲染。</p>
        </div>
        <span class="muted">{{ examples.filter((x) => x.pdb).length }} 个条目</span>
      </div>
      <div class="pdb-grid">
        <button
          v-for="ex in examples.filter((x) => x.pdb)"
          :key="ex.id"
          class="pdb-card"
          :class="{ active: viewerPdb === ex.pdb }"
          @click="fill(ex)"
        >
          <span class="pdb-id">{{ ex.pdb }}</span>
          <span class="pdb-name">{{ ex.name }}</span>
          <span class="pdb-meta">{{ ex.type }} · {{ ex.sequence.length }} nt</span>
        </button>
      </div>
    </section>

    <template v-if="result">
      <section class="card">
        <div class="card-head">
          <div>
            <h2>查询结果</h2>
            <template v-if="result.matched">
              <p class="hint" style="margin: 2px 0 0;">
                <span class="tag tag--teal">命中预置库</span>
                <strong style="margin-left: 6px;">{{ result.name }}</strong>
                <span class="badge">PDB {{ result.pdb }}</span>
              </p>
            </template>
          </div>
          <span v-if="!result.matched" class="tag">
            {{ result.source === 'rcsb' ? 'RCSB 相似结构' : '未找到实验结构' }}
          </span>
        </div>

        <template v-if="result.matched">
          <p class="muted">{{ result.annotation }}</p>
        </template>
        <template v-else>
          <p>{{ result.message }}</p>
          <p v-if="result.rcsb_error" class="hint">RCSB 错误：{{ result.rcsb_error }}</p>
        </template>

        <MolstarViewer v-if="viewerUrl" :url="viewerUrl" :height="560" />
        <p v-if="viewerUrl" class="hint">
          3D 视图由内嵌 Mol* 渲染（当前 PDB <span class="mono">{{ viewerPdb }}</span>）。
          <template v-if="viewerLocal">坐标来自本地预览库 <span class="mono">/api/pdb/{{ viewerPdb }}</span>，内网与离线可用。</template>
          <template v-else>该结构未收录到本地，坐标从 RCSB 实时下载。</template>
          <a :href="`https://www.rcsb.org/structure/${viewerPdb}`" target="_blank" rel="noopener noreferrer">打开 RCSB 页面</a>
        </p>
      </section>

      <section v-if="result.similar && result.similar.length" class="card">
        <div class="card-head">
          <div>
            <h2>相似实验结构</h2>
            <p class="hint" style="margin: 2px 0 0;">序列相似度 ≥90%，点击任意一条切换上方 3D 视图。</p>
          </div>
        </div>
        <div class="similar-grid">
          <button
            v-for="item in result.similar"
            :key="item.pdb_id"
            class="similar-card"
            :class="{ active: viewerPdb === item.pdb_id }"
            @click="pickSimilar(item)"
          >
            <span class="similar-id">{{ item.pdb_id }}</span>
            <span class="similar-title">{{ item.title || '（无标题）' }}</span>
            <span class="similar-meta">{{ resolutionText(item.resolution) }} · {{ item.method || '未知方法' }}</span>
            <span class="similar-score">{{ scoreText(item.score) }}</span>
          </button>
        </div>
      </section>

      <section v-if="result.rnacomposer" class="card">
        <div class="card-head">
          <h2>用 RNAComposer 预测（手动）</h2>
          <span class="tag tag--amber">需要外网</span>
        </div>
        <p class="muted">
          RNAComposer 官方未提供公开 REST API，因此本站无法自动提交，真实使用途径是在
          官网手动操作：粘贴下方输入（第 1 行任务名、第 2 行序列、第 3 行 dot-bracket，可选；
          交互模式限长 500 nt）→ 提交任务 → 等待邮件通知 → 下载 PDB，再回本站用上方 Mol* 查看。
        </p>
        <pre class="bridge">{{ result.rnacomposer.input }}</pre>
        <div class="inline wrap">
          <button class="secondary" @click="copyBridge">{{ copied ? '已复制 ✓' : '复制输入' }}</button>
          <a :href="result.rnacomposer.url" target="_blank" rel="noopener noreferrer" class="link-btn">
            打开 RNAComposer
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5.5A1.5 1.5 0 0 1 16.5 21h-11A1.5 1.5 0 0 1 4 19.5v-11A1.5 1.5 0 0 1 5.5 7H11" /></svg>
          </a>
        </div>
        <p class="hint">在官网运行后下载 PDB，可再用 Mol* 打开该文件查看。</p>
      </section>
    </template>

    <section class="card">
      <div class="card-head">
        <div>
          <h2>本地生成 3D（离线简化模型）</h2>
          <p class="hint" style="margin: 2px 0 0;">以二级结构为约束求解粗粒度坐标，秒级出图，不依赖外网。</p>
        </div>
        <span class="tag tag--teal">内置建模器</span>
      </div>
      <p class="muted">
        输出包含 P / O5' / C5' / C4' / C3' / O3' 的完整糖-磷酸骨架。
        精度低于 RNAComposer / RhoFold+，适合快速查看拓扑与相对位置。
      </p>
      <div class="inline wrap" style="margin-top: 12px;">
        <button :disabled="building || !normalized.length || over3dLimit" @click="buildLocal">
          <span v-if="building" class="spinner spinner--light"></span>
          {{ localModel ? '重新生成 3D 模型' : '生成 3D 模型' }}
        </button>
        <button v-if="building" class="secondary" @click="cancelLocal">取消</button>
        <span v-if="building" class="hint" style="margin: 0;">进度：{{ localStage || '排队中…' }}</span>
        <span v-else-if="over3dLimit" class="hint hint--warn" style="margin: 0;">
          本地建模限长 {{ maxLen3d }} nt（当前 {{ normalized.length }} nt），请缩短序列后再生成
        </span>
      </div>
      <p v-if="localError" class="error">{{ localError }}</p>
      <p v-if="localNote" class="hint note-warn">注意：{{ localNote }}</p>

      <template v-if="localModel">
        <MolstarViewer :url="localModel.url" :height="520" />
        <p class="hint">
          来源：{{ localModel.engine === 'external' ? '外部 3D 引擎' : '内置简化建模' }}
          · {{ localModel.atoms }} 个原子 · 耗时 {{ localModel.elapsed }} s
          <template v-if="localModel.cached">（缓存命中）</template>
          · <a :href="localModel.url" target="_blank" rel="noopener noreferrer">下载 PDB</a>
        </p>
        <p v-if="localModel.fallback" class="hint">{{ localModel.fallback }}</p>
      </template>
    </section>

    <section class="card card--quiet">
      <div class="card-head">
        <h2>进阶能力：本地部署预测引擎</h2>
        <span class="tag">可选部署</span>
      </div>
      <p class="muted">
        对既无实验结构、RCSB 也检索不到相似结构的序列，真实途径是自行部署专用预测引擎，
        预测完成后把 PDB 文件放到本站 <span class="mono">data/pdb/</span> 目录即可离线查看：
      </p>
      <ul class="hint" style="margin: 0 0 12px; padding-left: 18px; line-height: 1.9;">
        <li><strong>RoseTTAFold All-Atom / NA</strong> —— 精度最高，但需数百 MB 至 GB 级权重与较高算力；</li>
        <li><strong>RhoFold+</strong> —— RNA-FM 驱动、轻量得多，单卡 CPU 即可运行，适合快速出图。</li>
      </ul>
      <div class="inline wrap">
        <a
          href="https://github.com/baker-laboratory/RoseTTAFold-All-Atom"
          target="_blank"
          rel="noopener noreferrer"
          class="link-btn secondary"
        >
          RoseTTAFold All-Atom
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5.5A1.5 1.5 0 0 1 16.5 21h-11A1.5 1.5 0 0 1 4 19.5v-11A1.5 1.5 0 0 1 5.5 7H11" /></svg>
        </a>
        <a
          href="https://github.com/ml4bio/RhoFold+"
          target="_blank"
          rel="noopener noreferrer"
          class="link-btn secondary"
        >
          RhoFold+
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5.5A1.5 1.5 0 0 1 16.5 21h-11A1.5 1.5 0 0 1 4 19.5v-11A1.5 1.5 0 0 1 5.5 7H11" /></svg>
        </a>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ter-input {
  margin-top: 16px;
  padding: 4px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface-2);
}
.ter-input textarea {
  border: none;
  background: transparent;
  box-shadow: none;
  min-height: 96px;
  padding: 10px 12px;
}
.ter-input:focus-within { border-style: solid; border-color: rgba(99, 102, 241, 0.35); background: #fff; }
.ter-input textarea:focus { box-shadow: none; background: transparent; }

.ter-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 14px; }

/* 预置库 */
.pdb-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(178px, 1fr));
}
.pdb-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 12px 14px;
  min-height: 0;
  border-radius: var(--radius);
  background: var(--surface-2);
  border: 1px solid var(--border);
  box-shadow: none;
  color: var(--text);
  text-align: left;
  white-space: normal;
  transition: border-color 0.18s var(--ease), background 0.18s var(--ease), transform 0.18s var(--ease);
}
.pdb-card:hover:not(:disabled) {
  transform: translateY(-2px);
  background: #fff;
  border-color: rgba(99, 102, 241, 0.32);
  box-shadow: 0 12px 26px -20px rgba(30, 41, 59, 0.5);
}
.pdb-card.active { border-color: var(--brand); background: var(--brand-soft); }
.pdb-id {
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--brand);
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid rgba(99, 102, 241, 0.2);
  border-radius: 5px;
  padding: 1px 5px;
}
.pdb-name { font-size: 0.86rem; font-weight: 620; line-height: 1.4; }
.pdb-meta { font-size: 0.72rem; color: var(--muted); }

/* 相似结构 */
.similar-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
}
.similar-card {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 12px 14px;
  border-radius: var(--radius);
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: none;
  color: var(--text);
  text-align: left;
  white-space: normal;
}
.similar-card:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: rgba(99, 102, 241, 0.32);
  box-shadow: 0 12px 26px -22px rgba(30, 41, 59, 0.5);
}
.similar-card.active { border-color: var(--brand); background: var(--brand-soft); }
.similar-id { font-family: var(--mono); font-size: 0.8rem; font-weight: 700; color: var(--brand); }
.similar-title {
  font-size: 0.8rem;
  color: var(--text-2);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.similar-meta { font-size: 0.72rem; color: var(--muted); }
.similar-score { font-size: 0.72rem; color: var(--teal); font-weight: 600; }

.link-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 38px;
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 0.88rem;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #4f46e5, #6d5efc 60%, #7c3aed);
  box-shadow: 0 6px 16px -10px rgba(79, 70, 229, 0.9);
  border: 1px solid transparent;
  transition: transform 0.16s var(--ease), box-shadow 0.2s var(--ease);
}
.link-btn:hover { color: #fff; transform: translateY(-1px); box-shadow: 0 12px 24px -12px rgba(79, 70, 229, 0.85); }
.link-btn.secondary {
  background: var(--surface);
  color: var(--text-2);
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-xs);
}
.link-btn.secondary:hover { color: var(--brand-600); border-color: var(--brand-400); }
.link-btn + .link-btn { margin-left: 8px; }

.spinner--light { border-color: rgba(255, 255, 255, 0.4); border-top-color: #fff; }

.hint--warn { color: #b45309; }
.note-warn {
  margin-top: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  background: var(--amber-soft);
  border: 1px solid rgba(217, 119, 6, 0.22);
  color: #92400e;
}

@media (max-width: 640px) {
  .pdb-grid, .similar-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
