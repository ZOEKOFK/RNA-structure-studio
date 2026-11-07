<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { normalizeSequence, degenerateBases } from '../sequence'

const router = useRouter()
const sequence = ref('')
const examples = ref([])
const engine = ref('')
const version = ref('')
const error = ref('')
const maxLen = ref(1000)
const maxDeep = ref(300)

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  try {
    const [h, e] = await Promise.all([api.health(), api.examples()])
    engine.value = h.rnafold_available ? 'ViennaRNA（真实能量模型）' : '纯 Python 降级引擎'
    version.value = h.viennarna_version || ''
    maxLen.value = h.max_sequence_len || maxLen.value
    maxDeep.value = h.deep_max_len || maxDeep.value
    examples.value = e.examples
  } catch (e) {
    error.value = '后端未启动，请先运行 uvicorn backend.main:app --port 8000'
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
})

function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    if (invalid.value || !seq.value.length) return
    e.preventDefault()
    goFold()
  }
}

const seq = computed(() => normalizeSequence(sequence.value))
const degenerate = computed(() => degenerateBases(seq.value))
const invalid = computed(() => !!degenerate.value)

const gc = computed(() => {
  const s = seq.value
  if (!s.length) return 0
  const valid = [...s].filter((c) => 'ACGU'.includes(c)).length
  if (!valid) return 0
  const paired = [...s].filter((c) => c === 'G' || c === 'C').length
  return Math.round((paired / valid) * 1000) / 10
})

const strip = computed(() => [...seq.value.slice(0, 120)])
const stripRest = computed(() => Math.max(seq.value.length - 120, 0))

function goFold(seqOverride) {
  if (invalid.value) return
  const s = normalizeSequence(seqOverride !== undefined ? seqOverride : sequence.value)
  if (!s) return
  if (s.length > maxLen.value) {
    error.value = `长度 ${s.length} nt 超过实时折叠限长 ${maxLen.value} nt`
    return
  }
  router.push({ path: '/fold', query: { seq: s } })
}

function goMutate(seqOverride) {
  if (invalid.value) return
  const s = normalizeSequence(seqOverride !== undefined ? seqOverride : sequence.value)
  if (!s) return
  router.push({ path: '/mutate', query: { seq: s } })
}

function goTertiary(seqOverride) {
  if (invalid.value) return
  const s = normalizeSequence(seqOverride !== undefined ? seqOverride : sequence.value)
  router.push({ path: '/tertiary', query: s ? { seq: s } : {} })
}

function goDeep(seqOverride) {
  if (invalid.value) return
  const s = normalizeSequence(seqOverride !== undefined ? seqOverride : sequence.value)
  if (!s) return
  if (s.length > maxDeep.value) {
    error.value = `长度 ${s.length} nt 超过深度分析限长 ${maxDeep.value} nt`
    return
  }
  router.push({ path: '/deep', query: { seq: s } })
}

function useExample(seqText, target) {
  sequence.value = seqText
  error.value = ''
  if (target === 'fold') goFold(seqText)
  else if (target === 'mutate') goMutate(seqText)
  else if (target === 'deep') goDeep(seqText)
  else if (target === 'tertiary') goTertiary(seqText)
}

// 分子类型 → 卡片标签配色（纯视觉映射）
const TYPE_TONE = { tRNA: 'teal', rRNA: 'brand', 核酶: 'rose', 核糖开关: 'amber' }
function typeTone(type) {
  return TYPE_TONE[type] || 'muted'
}
</script>

<template>
  <div class="page">
    <!-- ========== Hero ========== -->
    <section class="hero">
      <div class="hero-copy">
        <span class="eyebrow">RNA Structure Lab</span>
        <h1 class="hero-title">
          从一条序列，读懂<br />
          <span class="gradient-text">RNA 的折叠方式</span>
        </h1>
        <p class="lead">
          输入序列即可实时得到二级结构、MFE 与配对概率；
          再对比突变、查看深度嵌入，或打开三维坐标。
        </p>
        <div class="hero-chips">
          <span class="chip">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
            实时折叠
          </span>
          <span class="chip">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 18V6M20 18V6M7 12h10" /></svg>
            ΔMFE 突变对比
          </span>
          <span class="chip">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12v9M12 12 4 7.5M12 12l8-4.5" /></svg>
            Mol* 三维视图
          </span>
        </div>
      </div>

      <div class="hero-visual" aria-hidden="true">
        <div class="specimen">
          <div class="specimen-top">
            <span class="specimen-dot"></span>
            <span>live specimen · tRNA-like hairpin</span>
          </div>

          <svg class="specimen-art" viewBox="0 0 300 220" fill="none">
            <defs>
              <linearGradient id="stemGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#818cf8" />
                <stop offset="55%" stop-color="#a78bfa" />
                <stop offset="100%" stop-color="#2dd4bf" />
              </linearGradient>
              <linearGradient id="loopGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#2dd4bf" />
                <stop offset="100%" stop-color="#818cf8" />
              </linearGradient>
              <radialGradient id="glow" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stop-color="#6366f1" stop-opacity="0.45" />
                <stop offset="100%" stop-color="#6366f1" stop-opacity="0" />
              </radialGradient>
            </defs>

            <circle cx="150" cy="112" r="96" fill="url(#glow)" />

            <!-- 骨架：一条折叠中的链 -->
            <path
              class="strand"
              d="M60 190 C60 150 96 148 96 118 C96 88 64 84 64 62 C64 42 86 34 104 34"
              stroke="url(#stemGrad)" stroke-width="3" stroke-linecap="round"
            />
            <path
              class="strand strand-delay"
              d="M96 190 C96 150 132 148 132 118 C132 88 100 84 100 62 C100 42 122 34 140 34"
              stroke="url(#stemGrad)" stroke-width="3" stroke-linecap="round" opacity="0.55"
            />

            <!-- 碱基对横档 -->
            <g class="rungs" stroke="#e2e8f0" stroke-width="2" stroke-linecap="round" opacity="0.55">
              <path d="M64 172h26M66 152h22M70 130h18M72 108h22M68 86h24M70 64h24" />
            </g>

            <!-- 环 -->
            <path
              d="M104 34 C104 14 168 8 196 30 C222 50 222 92 196 108 C176 120 150 114 140 96"
              stroke="url(#loopGrad)" stroke-width="3.2" stroke-linecap="round"
              stroke-dasharray="240 400"
            />
            <circle cx="196" cy="30" r="4.5" fill="#2dd4bf" />
            <circle cx="222" cy="70" r="4.5" fill="#818cf8" />
            <circle cx="140" cy="96" r="4.5" fill="#a78bfa" />

            <!-- 漂浮标签 -->
            <g class="float-tag" transform="translate(10 10)">
              <rect width="96" height="30" rx="8" fill="#ffffff" fill-opacity="0.08" stroke="#ffffff" stroke-opacity="0.16" />
              <text x="12" y="20" fill="#e2e8f0" font-size="12" font-family="ui-monospace, monospace">MFE −18.4</text>
            </g>
            <g class="float-tag float-delay" transform="translate(206 182)">
              <rect width="70" height="30" rx="8" fill="#ffffff" fill-opacity="0.08" stroke="#ffffff" stroke-opacity="0.16" />
              <text x="12" y="20" fill="#e2e8f0" font-size="12" font-family="ui-monospace, monospace">6 bp</text>
            </g>
          </svg>

          <div class="specimen-foot">
            <span>ACGU · 二级结构</span>
            <span class="specimen-metric">22 nt</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ========== 输入工作台 ========== -->
    <section class="studio">
      <div class="studio-head">
        <div>
          <span class="eyebrow">Step 01</span>
          <h2>输入或粘贴 RNA 序列</h2>
        </div>
        <div class="studio-engine">
          <span class="tag tag--teal">引擎</span>
          <strong>{{ engine || '检测中…' }}</strong>
          <span v-if="version" class="badge">v{{ version }}</span>
          <span class="studio-limit">限长 {{ maxLen }} nt</span>
        </div>
      </div>

      <div class="editor">
        <textarea
          v-model="sequence"
          spellcheck="false"
          placeholder="例如 GGGAAACCC，或粘贴 FASTA / 一整段 tRNA、5S rRNA 序列…"
        ></textarea>

        <div class="editor-bar">
          <div class="editor-stats">
            <span><strong>{{ seq.length }}</strong> nt</span>
            <span class="stat-sep"></span>
            <span>GC <strong>{{ gc }}%</strong></span>
            <span v-if="degenerate" class="stat-warn">含简并碱基 {{ degenerate }}</span>
          </div>
          <button v-if="seq.length" class="quiet" type="button" @click="sequence = ''">清空</button>
        </div>
      </div>

      <div v-if="strip.length" class="seq-strip" aria-hidden="true">
        <span
          v-for="(b, i) in strip"
          :key="i"
          class="b"
          :class="{
            'b-a': b === 'A',
            'b-u': b === 'U',
            'b-g': b === 'G',
            'b-c': b === 'C',
            'b-other': !'ACGU'.includes(b),
          }"
          :title="`${i + 1}: ${b}`"
        >{{ b }}</span>
        <span v-if="stripRest" class="more">+{{ stripRest }}</span>
      </div>

      <p v-if="degenerate" class="error">
        含简并碱基 {{ degenerate }}，无法预测结构；请先展开为 A / U / C / G。
      </p>
      <p v-if="error" class="error">{{ error }}</p>

      <div class="studio-actions">
        <button :disabled="invalid || !seq.length || seq.length > maxLen" @click="goFold()">
          预测二级结构
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
        </button>
        <button class="secondary" :disabled="invalid || !seq.length" @click="goMutate()">突变对比</button>
        <button class="secondary" :disabled="invalid || !seq.length" @click="goDeep()">深度分析</button>
        <button class="secondary" :disabled="invalid || !seq.length" @click="goTertiary()">3D 结构</button>
        <span class="kbd-hint"><kbd>Ctrl</kbd><span>+</span><kbd>Enter</kbd> 快速折叠</span>
      </div>
    </section>

    <!-- ========== 工作流 ========== -->
    <section class="flow">
      <div class="flow-step">
        <span class="flow-num">01</span>
        <div>
          <h3>清洗序列</h3>
          <p>大写、T→U、去 FASTA 头与空白。</p>
        </div>
      </div>
      <div class="flow-step">
        <span class="flow-num">02</span>
        <div>
          <h3>能量最小化</h3>
          <p>ViennaRNA 求 MFE 与点括号结构。</p>
        </div>
      </div>
      <div class="flow-step">
        <span class="flow-num">03</span>
        <div>
          <h3>扰动与对比</h3>
          <p>重折叠突变体，量化 ΔMFE 与配对差异。</p>
        </div>
      </div>
      <div class="flow-step">
        <span class="flow-num">04</span>
        <div>
          <h3>结构可视化</h3>
          <p>圈图、弧线图、概率热力图与 Mol* 三维。</p>
        </div>
      </div>
    </section>

    <!-- ========== 实例库 ========== -->
    <section>
      <div class="section-head">
        <div>
          <span class="eyebrow">Library</span>
          <h2>经典分子实例</h2>
        </div>
        <span class="muted">{{ examples.length }} 个预置分子 · 结构由 ViennaRNA 预测</span>
      </div>

      <div v-if="!examples.length" class="card empty">
        <div class="empty-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
        </div>
        正在读取预置实例…
      </div>

      <div v-else class="grid-auto examples-grid">
        <article
          v-for="(ex, i) in examples"
          :key="ex.id"
          class="example-card enter"
          :style="{ animationDelay: `${Math.min(i, 8) * 45}ms` }"
        >
          <div class="ex-top">
            <span class="ex-type" :class="'tone-' + typeTone(ex.type)">{{ ex.type }}</span>
            <span class="ex-len">{{ ex.sequence.length }} nt</span>
          </div>
          <h3 class="ex-name">{{ ex.name }}</h3>
          <div class="ex-meta">
            <span v-if="ex.pdb" class="ex-pdb">PDB {{ ex.pdb }}</span>
            <span v-else class="ex-pdb ex-pdb--none">无实验结构</span>
            <span v-if="ex.rfam" class="ex-rfam">{{ ex.rfam.accession }}</span>
          </div>
          <div class="ex-actions">
            <button class="secondary" @click="useExample(ex.sequence, 'fold')">折叠</button>
            <button class="secondary" @click="useExample(ex.sequence, 'mutate')">突变</button>
            <button class="secondary" @click="useExample(ex.sequence, 'deep')">深度</button>
            <button v-if="ex.pdb" class="secondary" @click="useExample(ex.sequence, 'tertiary')">3D</button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ---------- Hero ---------- */
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
  gap: 40px;
  align-items: center;
  padding: 34px 6px 42px;
}
.hero-copy h1.hero-title {
  font-size: clamp(2rem, 4vw, 2.9rem);
  font-weight: 760;
  letter-spacing: -0.035em;
  line-height: 1.16;
  margin: 14px 0 16px;
}
.hero-copy .lead { font-size: 1rem; max-width: 33em; color: var(--muted); line-height: 1.75; }
.hero-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 22px; }

/* ---------- 装饰卡片 ---------- */
.hero-visual { display: flex; justify-content: flex-end; }
.specimen {
  position: relative;
  width: 100%;
  max-width: 380px;
  border-radius: 24px;
  padding: 16px 16px 12px;
  background:
    radial-gradient(120% 90% at 78% 0%, rgba(99, 102, 241, 0.35), transparent 58%),
    radial-gradient(90% 70% at 0% 100%, rgba(13, 148, 136, 0.28), transparent 60%),
    #0b1526;
  border: 1px solid rgba(148, 163, 184, 0.22);
  box-shadow: 0 30px 70px -34px rgba(15, 23, 42, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.specimen::after {
  content: "";
  position: absolute;
  inset: 0;
  background-image: radial-gradient(rgba(148, 163, 184, 0.22) 1px, transparent 1px);
  background-size: 16px 16px;
  opacity: 0.35;
  pointer-events: none;
  mask-image: radial-gradient(circle at 65% 35%, #000, transparent 75%);
  -webkit-mask-image: radial-gradient(circle at 65% 35%, #000, transparent 75%);
}
.specimen-top {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 0.66rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #94a3b8;
}
.specimen-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #2dd4bf;
  box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.18);
  animation: pulse-dot 2.6s var(--ease) infinite;
}
.specimen-art { display: block; width: 100%; height: auto; margin: 6px 0 2px; }
.strand {
  stroke-dasharray: 320;
  stroke-dashoffset: 320;
  animation: draw 2.4s var(--ease) forwards;
}
.strand-delay { animation-delay: 0.25s; }
@keyframes draw { to { stroke-dashoffset: 0; } }
.rungs { animation: fade-in 0.9s 0.9s both; }
.float-tag { animation: float 5s ease-in-out infinite; }
.float-delay { animation-delay: 1.4s; }
@keyframes float {
  0%, 100% { translate: 0 0; }
  50% { translate: 0 -6px; }
}
.specimen-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  color: #94a3b8;
  border-top: 1px solid rgba(148, 163, 184, 0.16);
  padding-top: 10px;
  position: relative;
  z-index: 1;
}
.specimen-metric { color: #a5b4fc; }

/* ---------- 输入工作台 ---------- */
.studio {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 26px 28px 22px;
  box-shadow: var(--shadow-md);
  transition: box-shadow 0.3s var(--ease), border-color 0.3s var(--ease);
}
.studio:focus-within {
  border-color: rgba(99, 102, 241, 0.42);
  box-shadow: 0 0 0 5px rgba(99, 102, 241, 0.09), 0 26px 60px -34px rgba(79, 70, 229, 0.5);
}
.studio-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.studio-head h2 { margin: 6px 0 0; font-size: 1.12rem; }
.studio-engine {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 0.82rem;
  color: var(--text-2);
}
.studio-limit {
  color: var(--muted);
  font-size: 0.76rem;
  padding-left: 8px;
  border-left: 1px solid var(--border);
}

.editor {
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface-2);
  padding: 4px 4px 0;
  transition: border-color 0.2s var(--ease), background 0.2s var(--ease);
}
.studio:focus-within .editor { border-style: solid; border-color: rgba(99, 102, 241, 0.3); background: #fff; }
.editor textarea {
  border: none;
  background: transparent;
  box-shadow: none;
  min-height: 128px;
  padding: 12px 14px;
  font-size: 0.92rem;
  line-height: 1.85;
  letter-spacing: 0.05em;
}
.editor textarea:focus { box-shadow: none; background: transparent; }
.editor-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px 8px 14px;
  border-top: 1px solid var(--border);
  font-size: 0.78rem;
  color: var(--muted);
  flex-wrap: wrap;
}
.editor-stats { display: inline-flex; align-items: center; gap: 12px; flex-wrap: wrap; font-variant-numeric: tabular-nums; }
.editor-stats strong { color: var(--text); font-weight: 650; }
.stat-sep { width: 1px; height: 12px; background: var(--border-strong); }
.stat-warn { color: var(--rose); }
.editor-bar .quiet { font-size: 0.78rem; color: var(--muted); }

.studio-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 18px;
}
.kbd-hint {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.74rem;
  color: var(--muted-2);
}
kbd {
  font-family: var(--mono);
  font-size: 0.68rem;
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--surface-3);
  border: 1px solid var(--border);
  border-bottom-width: 2px;
  color: var(--text-2);
}

/* ---------- 工作流 ---------- */
.flow {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin: 26px 0 8px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.flow-step {
  display: flex;
  gap: 12px;
  padding: 18px 20px;
  border-right: 1px solid var(--border);
}
.flow-step:last-child { border-right: none; }
.flow-num {
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--brand);
  background: var(--brand-soft);
  border-radius: 6px;
  padding: 2px 6px;
  height: fit-content;
  letter-spacing: 0.04em;
}
.flow-step h3 { font-size: 0.88rem; margin: 0 0 3px; }
.flow-step p { font-size: 0.78rem; color: var(--muted); line-height: 1.55; margin: 0; }

/* ---------- 实例卡片 ---------- */
.examples-grid { grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
.example-card {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 16px 14px;
  box-shadow: var(--shadow-xs);
  transition: transform 0.22s var(--ease), box-shadow 0.22s var(--ease), border-color 0.22s var(--ease);
}
.example-card:hover {
  transform: translateY(-3px);
  border-color: rgba(99, 102, 241, 0.3);
  box-shadow: 0 18px 36px -24px rgba(30, 41, 59, 0.45);
}
.ex-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.ex-type {
  font-size: 0.68rem;
  font-weight: 620;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--teal);
  background: var(--teal-soft);
  border: 1px solid rgba(13, 148, 136, 0.16);
  border-radius: 999px;
  padding: 2px 8px;
}
.ex-type.tone-brand { color: var(--brand-600); background: var(--brand-soft); border-color: rgba(99, 102, 241, 0.16); }
.ex-type.tone-rose { color: #be123c; background: var(--rose-soft); border-color: rgba(225, 29, 72, 0.16); }
.ex-type.tone-amber { color: #b45309; background: var(--amber-soft); border-color: rgba(217, 119, 6, 0.18); }
.ex-type.tone-muted { color: var(--muted); background: var(--surface-3); border-color: var(--border); }
.ex-len { font-size: 0.74rem; color: var(--muted-2); font-variant-numeric: tabular-nums; }
.ex-name { font-size: 0.96rem; font-weight: 660; margin: 0 0 6px; letter-spacing: -0.01em; }
.ex-meta { display: flex; gap: 8px; flex-wrap: wrap; font-size: 0.74rem; color: var(--muted); margin-bottom: 14px; }
.ex-pdb { font-family: var(--mono); }
.ex-pdb--none { color: var(--muted-2); }
.ex-rfam { color: var(--brand); }
.ex-actions { display: flex; gap: 6px; margin-top: auto; flex-wrap: wrap; }
.ex-actions button {
  flex: 1;
  min-height: 32px;
  padding: 4px 8px;
  font-size: 0.78rem;
  font-weight: 550;
  border-radius: 8px;
}

@media (max-width: 980px) {
  .hero { grid-template-columns: 1fr; gap: 26px; padding: 14px 2px 30px; }
  .hero-visual { justify-content: flex-start; }
  .specimen { max-width: 100%; }
  .flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flow-step:nth-child(2) { border-right: none; }
  .flow-step:nth-child(1), .flow-step:nth-child(2) { border-bottom: 1px solid var(--border); }
}
@media (max-width: 560px) {
  .studio { padding: 18px 16px 16px; }
  .flow { grid-template-columns: 1fr; }
  .flow-step { border-right: none; border-bottom: 1px solid var(--border); }
  .flow-step:last-child { border-bottom: none; }
  .kbd-hint { display: none; }
  .studio-actions button { flex: 1 1 45%; }
}
</style>
