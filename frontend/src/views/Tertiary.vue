<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { normalizeSequence } from '../sequence'

const route = useRoute()
const sequence = ref('')
const examples = ref([])
const result = ref(null)
const loading = ref(false)
const error = ref('')
const selectedPdb = ref('')
const copied = ref(false)
const controller = ref(null)
const iframeReady = ref(false)
const iframeSlow = ref(false)
let iframeTimer = null

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

const viewerPdb = computed(
  () => selectedPdb.value || (result.value && result.value.pdb) || ''
)

const viewerUrl = computed(() =>
  viewerPdb.value ? `https://molstar.org/viewer/?pdb=${viewerPdb.value}` : ''
)

// Mol* 为外部页面，网络受限时 iframe 可能长时间空白：10 秒未加载完给出提示
watch(viewerUrl, (url) => {
  iframeReady.value = false
  iframeSlow.value = false
  clearTimeout(iframeTimer)
  if (url) {
    iframeTimer = setTimeout(() => {
      if (!iframeReady.value) iframeSlow.value = true
    }, 10000)
  }
})

onUnmounted(() => {
  clearTimeout(iframeTimer)
  if (controller.value) controller.value.abort()
})

function onIframeLoad() {
  iframeReady.value = true
  iframeSlow.value = false
}

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

async function run() {
  if (!normalized.value) return
  cancel()
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
  <div class="container">
    <div class="card">
      <h1>三维结构查看（Mol*）</h1>
      <p class="muted">
        先比对预置结构库；未命中时用 RCSB PDB 检索序列相似度 ≥90% 的已解析实验结构；
        仍无结果则生成 RNAComposer 输入文本，在官方网页手动预测。
      </p>
      <textarea v-model="sequence" placeholder="输入 RNA 序列，或点击下方实例…"></textarea>
      <div style="margin-top: 10px; display: flex; gap: 10px;">
        <button :disabled="loading || !normalized.length" @click="run">查询 3D 结构</button>
        <button v-if="loading" class="secondary" @click="cancel">取消查询</button>
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
          <span class="tag">命中预置库</span><strong>{{ result.name }}</strong>
          <span class="badge">PDB {{ result.pdb }}</span>
          <p class="hint">{{ result.annotation }}</p>
        </template>
        <template v-else>
          <span class="tag">
            {{ result.source === 'rcsb' ? 'RCSB 相似结构' : '未找到实验结构' }}
          </span>
          <p>{{ result.message }}</p>
          <p v-if="result.rcsb_error" class="hint">RCSB 错误：{{ result.rcsb_error }}</p>
        </template>

        <iframe
          v-if="viewerUrl"
          :src="viewerUrl"
          style="width: 100%; height: 560px; border: 1px solid var(--border); border-radius: 8px; margin-top: 10px;"
          allowfullscreen
          @load="onIframeLoad"
        ></iframe>
        <p v-if="viewerUrl" class="hint">
          3D 视图由 Mol* Viewer 提供（当前 PDB {{ viewerPdb }}），数据来自 RCSB PDB。
          <a :href="viewerUrl" target="_blank" rel="noopener noreferrer">在新窗口打开</a>
        </p>
        <p v-if="iframeSlow" class="hint">
          视图 10 秒内未加载完成，通常是内网或离线无法访问 molstar.org；
          可点击上方「在新窗口打开」重试，或直接访问
          <a :href="`https://www.rcsb.org/structure/${viewerPdb}`" target="_blank" rel="noopener noreferrer">RCSB 页面</a>。
        </p>
      </div>

      <div v-if="result.similar && result.similar.length" class="card">
        <h2>相似实验结构</h2>
        <p class="hint">序列相似度 ≥90%，点击任一条切换上方 3D 视图。</p>
        <div class="row">
          <div v-for="item in result.similar" :key="item.pdb_id" class="col">
            <button
              class="secondary"
              style="width: 100%;"
              :style="viewerPdb === item.pdb_id ? 'border-color: var(--accent, #4f8); font-weight: 600;' : ''"
              @click="pickSimilar(item)"
            >{{ item.pdb_id }}</button>
            <div class="hint">
              {{ item.title || '（无标题）' }}<br />
              {{ resolutionText(item.resolution) }} · {{ item.method || '未知方法' }}<br />
              {{ scoreText(item.score) }}
            </div>
          </div>
        </div>
      </div>

      <div v-if="result.rnacomposer" class="card">
        <h2>用 RNAComposer 预测（手动）</h2>
        <p class="muted">
          RNAComposer 无公开 REST API，需在官方网页粘贴输入：
          第 1 行任务名、第 2 行序列，第 3 行 dot-bracket 结构（可选）。
        </p>
        <pre class="bridge">{{ result.rnacomposer.input }}</pre>
        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
          <button class="secondary" @click="copyBridge">{{ copied ? '已复制' : '复制输入' }}</button>
          <a
            :href="result.rnacomposer.url"
            target="_blank"
            rel="noopener noreferrer"
            style="text-decoration: none;"
          >
            <button>打开 RNAComposer</button>
          </a>
        </div>
        <p class="hint">在官网运行后下载 PDB，可再用 Mol* 打开该文件查看。</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bridge {
  background: var(--panel-2, #212c42);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  margin: 10px 0;
}
</style>
