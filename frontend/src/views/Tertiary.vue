<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { normalizeSequence } from '../sequence'

const route = useRoute()
const sequence = ref('')
const examples = ref([])
const result = ref(null)
const loading = ref(false)
const error = ref('')

const normalized = computed(() => normalizeSequence(sequence.value))

onMounted(async () => {
  const s = normalizeSequence(route.query.seq || '')
  try {
    const e = await api.examples()
    examples.value = e.examples
  } catch (e) {
    error.value = '后端未启动，请先运行 uvicorn backend.main:app --port 8000'
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

const viewerUrl = computed(() => {
  const pdb = result.value && result.value.pdb
  return pdb ? `https://molstar.org/viewer/?pdb=${pdb}` : ''
})

async function run() {
  if (!normalized.value) return
  loading.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.tertiary(normalized.value)
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
      <h1>三维结构查看（Mol*）</h1>
      <p class="muted">
        输入序列先与预置结构库比对，命中即刻展示已知 3D 结构；
        未命中则提示接入 RNAComposer API（方案阶段 4）。
      </p>
      <textarea v-model="sequence" placeholder="输入 RNA 序列，或点击下方实例…"></textarea>
      <div style="margin-top: 10px; display: flex; gap: 10px;">
        <button :disabled="loading || !normalized.length" @click="run">查询 3D 结构</button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </div>

    <div class="card">
      <h2>预置结构库</h2>
      <div class="row">
        <div v-for="ex in examples.filter(x => x.pdb)" :key="ex.id" class="col">
          <button class="secondary" style="width: 100%;" @click="fill(ex)">{{ ex.name }}</button>
          <div class="hint">PDB {{ ex.pdb }} · {{ ex.sequence.length }} nt</div>
        </div>
      </div>
      <p class="hint">仅含已解析 PDB 的条目可在线 3D 查看。</p>
    </div>

    <template v-if="result">
      <div class="card">
        <h2>查询结果</h2>
        <template v-if="result.matched">
          <span class="tag">命中</span><strong>{{ result.name }}</strong>
          <span class="badge">PDB {{ result.pdb }}</span>
          <p class="hint">{{ result.annotation }}</p>
          <iframe
            v-if="viewerUrl"
            :src="viewerUrl"
            style="width: 100%; height: 560px; border: 1px solid var(--border); border-radius: 8px; margin-top: 10px;"
            allowfullscreen
          ></iframe>
          <p class="hint">3D 视图由 Mol* Viewer 提供，数据来自 RCSB PDB。</p>
        </template>
        <template v-else>
          <p>{{ result.message }}</p>
        </template>
      </div>
    </template>
  </div>
</template>