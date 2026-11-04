<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const sequence = ref('')
const examples = ref([])
const engine = ref('')
const version = ref('')
const error = ref('')
const maxLen = 1000
const maxDeep = 300

onMounted(async () => {
  try {
    const [h, e] = await Promise.all([api.health(), api.examples()])
    engine.value = h.rnafold_available ? 'ViennaRNA（真实能量模型）' : '纯 Python 降级引擎'
    version.value = h.viennarna_version || ''
    examples.value = e.examples
  } catch (e) {
    error.value = '后端未启动，请先运行 uvicorn backend.main:app --port 8000'
  }
})

function normalize(v) {
  return v.toUpperCase().replace(/T/g, 'U').replace(/[^AUCG]/g, '')
}

function goFold(seq) {
  const s = normalize(seq !== undefined ? seq : sequence.value)
  if (!s) return
  if (s.length > maxLen) {
    error.value = `长度 ${s.length} nt 超过实时折叠限长 ${maxLen} nt`
    return
  }
  router.push({ path: '/fold', query: { seq: s } })
}

function goMutate(seq) {
  const s = normalize(seq !== undefined ? seq : sequence.value)
  if (!s) return
  router.push({ path: '/mutate', query: { seq: s } })
}

function goTertiary(seq) {
  const s = normalize(seq !== undefined ? seq : sequence.value)
  router.push({ path: '/tertiary', query: s ? { seq: s } : {} })
}

function goDeep(seq) {
  const s = normalize(seq !== undefined ? seq : sequence.value)
  if (!s) return
  if (s.length > maxDeep) {
    error.value = `长度 ${s.length} nt 超过深度分析限长 ${maxDeep} nt`
    return
  }
  router.push({ path: '/deep', query: { seq: s } })
}
</script>

<template>
  <div class="container">
    <div class="card">
      <h1>输入 RNA 序列</h1>
      <p class="muted">
        当前折叠引擎：<strong>{{ engine || '检测中…' }}</strong>
        <span v-if="version">（ViennaRNA {{ version }}）</span>。
        支持 A / U / G / C（自动把 T 转成 U），实时折叠限长 {{ maxLen }} nt。
      </p>
      <textarea
        v-model="sequence"
        placeholder="例如：GGGAAACCC 或粘贴一段 tRNA / 5S rRNA 序列…"
      ></textarea>
      <div class="hint">{{ normalize(sequence).length }} nt</div>
      <p v-if="error" class="error">{{ error }}</p>
      <div style="margin-top: 12px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
        <button :disabled="!normalize(sequence).length || normalize(sequence).length > maxLen" @click="goFold()">
          预测二级结构 →
        </button>
        <button class="secondary" @click="goMutate()">突变对比</button>
        <button class="secondary" @click="goDeep()">深度分析</button>
      </div>
    </div>

    <div class="card">
      <h2>一键加载经典实例</h2>
      <p class="muted" style="margin-top: 0;">{{ examples.length }} 个预置分子，结构由 ViennaRNA 预测。</p>
      <div class="row">
        <div v-for="ex in examples" :key="ex.id" class="example-card">
          <div class="ex-name">{{ ex.name }}</div>
          <div class="hint">{{ ex.type }} · {{ ex.sequence.length }} nt
            <span v-if="ex.pdb"> · PDB {{ ex.pdb }}</span>
          </div>
          <div class="ex-actions">
            <button class="secondary" @click="goFold(ex.sequence)">折叠</button>
            <button class="secondary" @click="goMutate(ex.sequence)">突变</button>
            <button class="secondary" @click="goDeep(ex.sequence)">深度</button>
            <button v-if="ex.pdb" class="secondary" @click="goTertiary(ex.sequence)">3D</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.example-card {
  flex: 1 1 220px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  background: var(--panel-2);
}
.ex-name { font-weight: 600; margin-bottom: 4px; }
.ex-actions { display: flex; gap: 6px; margin-top: 10px; }
.ex-actions button { flex: 1; padding: 6px 8px; font-size: 0.85rem; }
</style>