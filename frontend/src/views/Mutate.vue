<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
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

onMounted(() => {
  const s = (route.query.seq || '').toUpperCase().replace(/T/g, 'U')
  sequence.value = s.replace(/[^AUCG]/g, '')
})

const normalized = computed(() => sequence.value.toUpperCase().replace(/T/g, 'U').replace(/[^AUCG]/g, ''))
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
  try {
    result.value = await api.mutate(s, Number(position.value), base.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
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
    </template>
  </div>
</template>

<style scoped>
.trend { margin-left: 8px; font-weight: 600; font-size: 0.9rem; }
.trend.good { color: var(--accent); }
.trend.bad { color: var(--red); }
</style>